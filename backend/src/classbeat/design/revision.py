"""The ``draft`` revision of ``design_draft`` (ADR 0006 sections 3 and 6, ADR 0005 section 1).

- **content**: the submitted ``WorkoutDraft`` as fitted to the music: its interval lengths and
  ``"beat"`` cadences are the ones ``sync.fit_to_music`` computed (``classbeat.agent.design``),
  never a number the model stated. ``workout.validate()`` runs on it again just before it is
  stored. ``content_hash`` is SHA-256 of its canonical JSON (ADR 0004 section 2's rule: sorted
  keys, no spaces, UTF-8).
- **music**: ``{source: "mowl_spotify_playlist", link_id, fingerprint, unknown_bpm}`` (plus
  ``song_list_id``, the approved ``song_list`` revision, on a draft of ``design_with_songs``):
  the user's ``mowl_playlist_links`` row, the fingerprint of the analysed track list
  (``classbeat.design.music.fingerprint``) and the tracks MOWL had no BPM for yet, by index and
  title, so the user is told (their bars were a guess at 120 BPM). No MOWL or Spotify id, no map.
  The revision is the user's own and is deleted with its class or account (ADR 0010).
- **provenance**: ``classbeat.agent.records.provenance``: provider, model, effort, the prompt and
  tool-schema version, classbeat's and mowl-workouts' versions, ``map_version`` and the map's
  ``crossfade_sec`` next to it, the ``agent_runs`` ids, usage and cost.

``store`` runs inside the job's final fenced transaction (``Complete``), so the revision, its
``revision_created`` event and ``completed`` commit together or not at all. ``adopt_first`` runs
in the same transaction after it (ADR 0007 section 2): the class's first design sets
``classes.brief``, ``music_source`` and ``current_revision_id``, compare-and-set from NULL.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID

from mowl import workout
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text

from classbeat.agent import DesignResult, Provenance, WorkoutDraft, provenance
from classbeat.agent.loop import StageOutcome
from classbeat.design.music import MusicStep
from classbeat.jobs import FencedSession, JobFailure

MUSIC_SOURCE: Final = "mowl_spotify_playlist"
MAX_CONTENT_BYTES: Final = 65536  # the class_revisions.content check of migration 0004
MAX_BRIEF_BYTES: Final = 4096  # the classes.brief check of migration 0005

# The parent, when given, must be a revision of the job's own class (the schema's foreign key
# checks only the owner), else no row is inserted.
_INSERT = text(
    "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
    "music, provenance, job_id, parent_id) "
    "SELECT :id, j.owner_id, j.class_id, coalesce((SELECT max(r.number) FROM class_revisions r "
    "WHERE r.owner_id = j.owner_id AND r.class_id = j.class_id), 0) + 1, 'draft', "
    "CAST(:content AS jsonb), :content_hash, CAST(:music AS jsonb), CAST(:provenance AS jsonb), "
    "j.id, p.id FROM jobs j LEFT JOIN class_revisions p ON p.id = CAST(:parent_id AS uuid) "
    "AND p.owner_id = j.owner_id AND p.class_id = j.class_id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id "
    "AND (CAST(:parent_id AS uuid) IS NULL OR p.id IS NOT NULL) RETURNING number"
)

# The job's own class (read from the fenced job row, never from the input). All three are set
# together and only while the class has no current revision: its first design.
_ADOPT_FIRST = text(
    "UPDATE classes c SET current_revision_id = :revision_id, brief = CAST(:brief AS jsonb), "
    "music_source = CAST(:music_source AS jsonb) FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id IS NULL RETURNING c.id"
)


async def adopt_first(
    fs: FencedSession, revision_id: UUID, brief: dict[str, Any], music_source: dict[str, Any]
) -> bool:
    """In the final fenced transaction, after the revision is stored: make it the class's current
    revision and record the brief and the music source, if the class has none yet. Returns
    whether it did (a class that already has a current revision keeps it)."""
    found = await fs.db.execute(
        _ADOPT_FIRST,
        {
            "revision_id": revision_id,
            "brief": json.dumps(brief, sort_keys=True, ensure_ascii=False),
            "music_source": json.dumps(music_source, sort_keys=True, ensure_ascii=False),
            "job_id": fs.lease.job_id,
            "owner_id": fs.lease.owner_id,
        },
    )
    return found.first() is not None


class UnknownBpmTrack(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    index: int = Field(ge=0)
    title: str = Field(max_length=200)


class RevisionMusic(BaseModel):
    """``class_revisions.music`` of a draft."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["mowl_spotify_playlist"]
    link_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    unknown_bpm: list[UnknownBpmTrack] = Field(max_length=500)
    # ADR 0008 section 4: a draft designed on picked songs names the approved ``song_list``
    # revision it was made from; absent on every other draft (stored exactly as before).
    song_list_id: UUID | None = None


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def fitted_draft(result: DesignResult) -> WorkoutDraft:
    """The submitted draft with the fitted lengths and cadences (cues and names kept)."""
    data = result.draft.model_dump(mode="python")
    segments = result.spec["segments"]
    if len(segments) != len(data["segments"]):
        raise JobFailure("internal")
    for seg, fitted in zip(data["segments"], segments, strict=True):
        if len(seg["intervals"]) != len(fitted["intervals"]):
            raise JobFailure("internal")
        for interval, f in zip(seg["intervals"], fitted["intervals"], strict=True):
            interval["sec"] = int(f["sec"])
            interval["rpm"] = [int(f["rpm"][0]), int(f["rpm"][1])]
    try:
        return WorkoutDraft.model_validate(data)
    except ValidationError:
        raise JobFailure("invalid_output") from None


@dataclass(frozen=True)
class DraftRevision:
    content: dict[str, Any]
    content_hash: bytes
    music: dict[str, Any]
    provenance: dict[str, Any]
    parent_id: UUID | None = None  # the base of a revise; None for a design

    async def store(self, fs: FencedSession) -> UUID:
        revision_id = uuid.uuid4()
        found = (
            await fs.db.execute(
                _INSERT,
                {
                    "id": revision_id,
                    "content": json.dumps(self.content, sort_keys=True, ensure_ascii=False),
                    "content_hash": self.content_hash,
                    "music": json.dumps(self.music, sort_keys=True, ensure_ascii=False),
                    "provenance": json.dumps(self.provenance, sort_keys=True),
                    "parent_id": self.parent_id,
                    "job_id": fs.lease.job_id,
                    "owner_id": fs.lease.owner_id,
                },
            )
        ).first()
        if found is None:  # the job's row or the parent is not what the input said
            raise JobFailure("internal")
        number = found.number
        await fs.event(
            "revision_created", {"revision_id": revision_id, "kind": "draft", "number": number}
        )
        return revision_id


def build(
    result: DesignResult,
    outcome: StageOutcome,
    music_map: dict[str, Any],
    step: MusicStep,
    *,
    origin: Literal["design_draft", "revise_draft", "design_with_songs"] = "design_draft",
    base_revision_id: UUID | None = None,
    base_hash: bytes | None = None,
    request_message_id: UUID | None = None,
    song_list_id: UUID | None = None,
) -> DraftRevision:
    """The revision to store; ``invalid_output`` if the draft no longer validates, or (a revise)
    if it is the base draft again: a revision that changes nothing is not stored."""
    draft = fitted_draft(result)
    try:
        workout.validate(draft.to_spec())  # again, server-side, before it is stored
    except ValueError:
        raise JobFailure("invalid_output") from None
    content = draft.model_dump(mode="json", exclude_none=True)
    if len(canonical(content)) > MAX_CONTENT_BYTES:
        raise JobFailure("invalid_output")
    music = RevisionMusic(
        source=MUSIC_SOURCE,
        link_id=step.link_id,
        fingerprint=step.fingerprint,
        unknown_bpm=[UnknownBpmTrack(index=u.index, title=u.title) for u in step.unknown_bpm],
        song_list_id=song_list_id,
    )
    content_hash = hashlib.sha256(canonical(content)).digest()
    if base_hash is not None and content_hash == base_hash:
        raise JobFailure("invalid_output")
    made: Provenance = provenance(
        [outcome],
        music_map,
        origin=origin,
        base_revision_id=base_revision_id,
        request_message_id=request_message_id,
    )
    return DraftRevision(
        content=content,
        content_hash=content_hash,
        music=music.model_dump(mode="json", exclude_none=True),
        provenance=made.model_dump(mode="json"),
        parent_id=base_revision_id,
    )
