"""The ``draft`` revision of ``design_draft`` (ADR 0006 sections 3 and 6, ADR 0005 section 1).

- **content**: the submitted ``WorkoutDraft`` as fitted to the music: its interval lengths and
  ``"beat"`` cadences are the ones ``sync.fit_to_music`` computed (``classbeat.agent.design``),
  never a number the model stated. ``workout.validate()`` runs on it again just before it is
  stored. A running class's content is a ``RunningDraft`` (ADR 0018: ``sec``, ``zone``,
  ``gradient``, ``cue``; no ``"sport"`` key, the class's brief says it), checked with
  ``workout.validate_running``; its provenance records ``sport: "running"``. ``content_hash``
  is SHA-256 of its canonical JSON (ADR 0004 section 2's rule: sorted keys, no spaces, UTF-8).
- **music**: ``{source: "mowl_spotify_playlist", link_id, fingerprint, unknown_bpm}`` (plus
  ``song_list_id``, the approved ``song_list`` revision, on a draft of ``design_with_songs``):
  the user's ``mowl_playlist_links`` row, the fingerprint of the analysed track list
  (``classbeat.design.music.fingerprint``) and the tracks MOWL had no BPM for yet, by index and
  title, so the user is told (their bars were a guess at 120 BPM). No MOWL or Spotify id, no map.
  A draft with a track on spotdown's sections adds ``spotdown`` (``revision_spotdown_model``).
  On a Swedebeat mix (ADR 0016 section 3, A4): ``{source: "mowl_swedebeat_mix", mix_id, name,
  duration, listed_duration, unsure_beat_one, fingerprint, link_id}`` (``SwedebeatRevisionMusic``):
  the fingerprint is the map's (``swedebeat_music.fingerprint``), and the link gives Save the MOWL
  playlist under the same checks as a Spotify link. Still no MOWL playlist id and no map.
  A soundless class (ADR 0007 amendment of 2026-11-03, item 7; #527): exactly
  ``{source: "none"}`` (``NoRevisionMusic``): no link, no fingerprint, no ``unknown_bpm``. Its
  provenance has ``map_version`` and ``crossfade_sec`` ``None``. ``REVISION_MUSIC`` (the two
  music shapes, with a link) does not take it; Save reads ``approval.SAVED_MUSIC``, which does,
  and writes such a revision with no playlist (SL3, #529). A SwedeBeat studio draft (ADR 0036
  section 1, SR C5): ``StudioRevisionMusic``, and its ``content_hash`` is ``studio_hash``'s.
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
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from mowl import workout
from pydantic import Field, TypeAdapter, ValidationError
from sqlalchemy import text

from classbeat.agent import DesignResult, Provenance, WorkoutDraft, beat_one, mix_input, provenance
from classbeat.agent.loop import StageOutcome
from classbeat.agent.schemas import AnyDraft, RunningDraft
from classbeat.design.music import MusicStep
from classbeat.design.music_kind import NoMusicStep
from classbeat.design.revision_adopt import adopt_first as adopt_first
from classbeat.design.revision_music_model import NoRevisionMusic as NoRevisionMusic
from classbeat.design.revision_music_model import RevisionSong
from classbeat.design.revision_music_model import SpotifyRevisionMusic as SpotifyRevisionMusic
from classbeat.design.revision_music_model import SwedebeatRevisionMusic as SwedebeatRevisionMusic
from classbeat.design.revision_music_model import UnknownBpmTrack as UnknownBpmTrack
from classbeat.design.revision_spotdown_model import SpotdownSections, sections_of
from classbeat.design.studio_revision_model import StudioRevisionMusic as StudioRevisionMusic
from classbeat.jobs import FencedSession, JobFailure

logger = logging.getLogger(__name__)

MUSIC_SOURCE: Final = "mowl_spotify_playlist"
MIX_SOURCE: Final = "mowl_swedebeat_mix"
NO_MUSIC_SOURCE: Final = "none"
MAX_CONTENT_BYTES: Final = 65536  # the class_revisions.content check of migration 0004
MAX_BRIEF_BYTES: Final = 4096  # the classes.brief check of migration 0005
# ADR 0007 amendment "beat-one corrections" item 5: canonical ``songs`` plus ``beat_one``.
MAX_BEAT_ONE_BYTES: Final = 131072

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


def studio_hash(content: Mapping[str, Any], mix_spec: Mapping[str, Any]) -> bytes:
    """A studio draft's ``content_hash`` (ADR 0036 section 1, amending ADR 0007 section 2): the
    SHA-256 of the canonical JSON ``{"content": ..., "mix_spec": ...}``."""
    return hashlib.sha256(canonical({"content": content, "mix_spec": mix_spec})).digest()


RevisionMusic = SpotifyRevisionMusic | SwedebeatRevisionMusic


def _beat_one_fields(
    music: RevisionMusic, step: MusicStep, music_map: dict[str, Any], duration_sec: int
) -> RevisionMusic:
    """ADR 0007 amendment "beat-one corrections" item 5: the corrections applied (absent when
    none) and the songs the class plays, from the corrected map it was fitted to."""
    applied = beat_one.as_list(mix_input.corrections_of(step.data))
    songs: list[dict[str, Any]] = []
    skipped = 0
    for entry in beat_one.songs(music_map, duration_sec):
        try:  # a BPM MOWL reported out of range (0.01, -5, 2000) drops that song's beat data
            songs.append(RevisionSong.model_validate(entry).model_dump(exclude_none=True))
        except ValidationError:
            skipped += 1
    if skipped:  # never fail a paid design for it; a count only
        logger.warning("beat-one songs without usable beat data", extra={"skipped": skipped})
    # The new fields only, never the whole ``music`` or ``unknown_bpm``: at most about 103 KB on
    # any valid map (200 songs, titles of 100 characters; 200 corrections), so this is a defence.
    if len(canonical({"beat_one": applied, "songs": songs})) > MAX_BEAT_ONE_BYTES:
        raise JobFailure("invalid_output")
    # Validated as a whole (not ``model_copy``, which skips the list bounds).
    whole = {**music.model_dump(mode="python"), "beat_one": applied or None, "songs": songs}
    return type(music).model_validate(whole)


def _sections(music_map: dict[str, Any] | None, list_id: UUID | None) -> SpotdownSections | None:
    try:  # a stamped track without its list cannot happen: fail closed
        return sections_of(music_map, list_id)
    except ValueError:
        raise JobFailure("internal") from None


REVISION_MUSIC: Final = TypeAdapter[RevisionMusic](
    Annotated[RevisionMusic, Field(discriminator="source")]
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def fitted_draft(result: DesignResult) -> AnyDraft:
    """The submitted draft with the fitted lengths and cadences (cues and names kept); a running
    draft with the fitted lengths (it has no cadence), read back with the running schema."""
    running = isinstance(result.draft, RunningDraft)
    data = result.draft.model_dump(mode="python")
    segments = result.spec["segments"]
    if len(segments) != len(data["segments"]):
        raise JobFailure("internal")
    for seg, fitted in zip(data["segments"], segments, strict=True):
        if len(seg["intervals"]) != len(fitted["intervals"]):
            raise JobFailure("internal")
        for interval, f in zip(seg["intervals"], fitted["intervals"], strict=True):
            interval["sec"] = int(f["sec"])
            if not running:
                interval["rpm"] = [int(f["rpm"][0]), int(f["rpm"][1])]
    try:
        return RunningDraft.model_validate(data) if running else WorkoutDraft.model_validate(data)
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


def checked_content(result: DesignResult) -> tuple[dict[str, Any], bool]:
    """The fitted content, validated again server-side, and whether it is a running draft;
    ``invalid_output`` if it no longer validates (also a composer's preview, which has no music)."""
    draft = fitted_draft(result)
    running = isinstance(draft, RunningDraft)
    try:  # again, server-side, before it is stored
        (workout.validate_running if running else workout.validate)(draft.to_spec())
    except ValueError:
        raise JobFailure("invalid_output") from None
    content = draft.model_dump(mode="json", exclude_none=True)
    if len(canonical(content)) > MAX_CONTENT_BYTES:
        raise JobFailure("invalid_output")
    return content, running


def content_and_music(
    result: DesignResult,
    music_map: dict[str, Any] | None,
    step: MusicStep | NoMusicStep,
    song_list_id: UUID | None,
    spotdown_list_id: UUID | None = None,
) -> tuple[dict[str, Any], bytes, RevisionMusic | NoRevisionMusic, bool]:
    """``checked_content``, its hash, the revision's music and whether it is a running draft.
    ``spotdown_list_id`` (a revise or refit): the list a stamped track came from, else
    ``song_list_id`` (``revision_spotdown_model``)."""
    content, running = checked_content(result)
    music: RevisionMusic | NoRevisionMusic
    if isinstance(step, NoMusicStep):
        if running or song_list_id is not None or music_map is not None:
            raise JobFailure("internal")  # soundless: a cycling draft on no music only
        music = NoRevisionMusic(source=NO_MUSIC_SOURCE)
    elif music_map is None:
        raise JobFailure("internal")  # a music step always has its map
    elif running and (step.mix is not None or song_list_id is not None):
        raise JobFailure("internal")  # running v1: an existing MOWL playlist only (ADR 0018)
    elif step.mix is not None:
        if song_list_id is not None:
            raise JobFailure("internal")  # a mix is never picked songs
        music = SwedebeatRevisionMusic(
            source=MIX_SOURCE,
            mix_id=step.mix.mix_id,
            name=step.mix.name,
            duration=step.mix.duration,
            listed_duration=step.mix.listed_duration,
            unsure_beat_one=list(step.mix.unsure_beat_one),
            fingerprint=step.fingerprint,
            link_id=step.link_id,
            analyser="spotdown" if step.mix.analyser == "spotdown" else None,
            provenance=step.mix.provenance,
        )
    else:
        music = SpotifyRevisionMusic(
            source=MUSIC_SOURCE,
            link_id=step.link_id,
            fingerprint=step.fingerprint,
            unknown_bpm=[UnknownBpmTrack(index=u.index, title=u.title) for u in step.unknown_bpm],
            song_list_id=song_list_id,
            spotdown=_sections(music_map, spotdown_list_id or song_list_id),
        )
    if not isinstance(step, NoMusicStep) and not isinstance(music, NoRevisionMusic):
        assert music_map is not None  # noqa: S101 - checked above
        music = _beat_one_fields(music, step, music_map, result.duration_sec)  # not soundless
    return content, hashlib.sha256(canonical(content)).digest(), music, running


def build(
    result: DesignResult,
    outcome: StageOutcome,
    music_map: dict[str, Any] | None,
    step: MusicStep | NoMusicStep,
    *,
    origin: Literal[
        "design_draft", "revise_draft", "design_with_songs", "revise_songs", "sketch"
    ] = "design_draft",
    base_revision_id: UUID | None = None,
    base_hash: bytes | None = None,
    request_message_id: UUID | None = None,
    song_list_id: UUID | None = None,
    rules: Mapping[str, Any] | None = None,
    spotdown_list_id: UUID | None = None,
) -> DraftRevision:
    """The revision to store; ``invalid_output`` if the draft no longer validates, or (a revise)
    if it is the base draft again: a revision that changes nothing is not stored."""
    content, content_hash, music, running = content_and_music(
        result, music_map, step, song_list_id, spotdown_list_id
    )
    if base_hash is not None and content_hash == base_hash:
        raise JobFailure("invalid_output")
    made: Provenance = provenance(
        [outcome],
        music_map,
        origin=origin,
        base_revision_id=base_revision_id,
        request_message_id=request_message_id,
        tss_approximate=result.tss_approximate,
        rules=rules,
        sport="running" if running else None,
    )
    return DraftRevision(
        content=content,
        content_hash=content_hash,
        music=music.model_dump(mode="json", exclude_none=True),
        provenance=made.model_dump(mode="json"),
        parent_id=base_revision_id,
    )
