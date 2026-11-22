"""A studio draft from the approved list's analyses (ADR 0036 sections 1 and 4, SR C5): the bridge
between the stage (which names tracks by index) and the stored revision (which names them by
catalogue reference).

- ``StudioMusic``: the approved tracks in the list's order, each with its reference and stored
  analysis, and the record of each analysis (``records``: the analyser, the bytes and spotdown's
  provenance, ADR 0040 section 6) that the draft keeps in its ``music.analyses``; a spotdown
  track's stage track carries its stored timing class, and so its cut candidates (#1328);
  ``stage_tracks`` is what the stage gets (the list's Spotify title and artist and the map, **no
  reference**), ``plan_of`` turns a stored mix spec back into the stage's indexes (a
  revise's base mix).
- ``build``: the fitted draft validated again; the stage's plan mapped to references and checked
  once more by ``mix_plan_policy`` against the stored analyses (the backend's own check, never
  the stage's word); its ``StudioRevisionMusic``; the ``content_hash`` (``revision_hash``) of
  ``{content, mix_spec}``, or with a revise's reconciled overlay ``{content, mix_spec, overlay}``,
  which ``edit_overlay`` stores. A revise whose hash is the base's is ``invalid_output``: a cut, the
  order or a crossfade alone is a change, the same draft and mix are not.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from mowl import workout
from pydantic import ValidationError

from classbeat.agent import provenance
from classbeat.agent.loop import StageOutcome
from classbeat.agent.studio_design import StudioResult
from classbeat.agent.studio_mix import StudioTrack
from classbeat.design import revision
from classbeat.design.edit_overlay_model import EditOverlay
from classbeat.design.revision import DraftRevision, StudioRevisionMusic
from classbeat.design.revision_hash import revision_hash
from classbeat.design.studio_revision_model import TrackRecord, map_hash
from classbeat.design.track_cache import TrackMap
from classbeat.jobs import JobFailure
from classbeat.spotdown.studio_map_model import track_timing_class
from classbeat.studio.mix_plan_policy import MixPlanRefused, TrackFacts, checked
from classbeat.studio.mix_spec_model import MixSpec
from classbeat.swedebeat_studio.song_list_model import SOURCE, StudioListContent

Origin = Literal["design_draft", "revise_draft"]


@dataclass(frozen=True)
class StudioMusic:
    """The approved list's tracks (no gap) with their analyses, in the list's order."""

    references: tuple[str, ...]
    tracks: tuple[StudioTrack, ...]
    records: tuple[TrackRecord, ...] = ()

    @classmethod
    def of(cls, content: StudioListContent, maps: Sequence[TrackMap]) -> "StudioMusic":
        by_reference = {m.reference: m for m in maps}
        refs: list[str] = []
        tracks: list[StudioTrack] = []
        records: list[TrackRecord] = []
        for song in content.songs:
            if song.swedebeat is None or song.swedebeat.reference not in by_reference:
                raise JobFailure("internal")  # the caller checked the list and analysed it
            found = by_reference[song.swedebeat.reference]
            refs.append(found.reference)
            timing = track_timing_class(found.analyser, found.provenance)
            tracks.append(StudioTrack(song.title, song.artist, found.music_map, timing))
            provenance = None if found.provenance is None else dict(found.provenance)
            records.append(TrackRecord(
                reference=found.reference,
                analyser=found.analyser,
                sha256=found.sha256,
                map_hash=map_hash(found.music_map),
                provenance=provenance,
            ))  # fmt: skip
        return cls(tuple(refs), tuple(tracks), tuple(records))

    def facts(self) -> dict[str, TrackFacts]:
        return {r: t.facts() for r, t in zip(self.references, self.tracks, strict=True)}

    def plan_of(self, spec: MixSpec) -> dict[str, Any]:
        """A stored mix spec by the stage's indexes (``internal`` for a reference off the list)."""
        index = {r: n for n, r in enumerate(self.references)}
        if any(t.reference not in index for t in spec.tracks):
            raise JobFailure("internal")
        return {
            "tracks": [
                {"track": index[t.reference], "start_sec": t.start_sec, "end_sec": t.end_sec}
                for t in spec.tracks
            ],
            "crossfades": list(spec.crossfades),
        }

    def spec_of(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        """The stage's plan by reference, checked again against the stored analyses;
        ``invalid_output`` for anything the backend would not make (an index off the list too)."""
        try:
            if any(type(t["track"]) is not int or t["track"] < 0 for t in plan["tracks"]):
                raise IndexError("a track is a list index")  # never -1 for the last one
            spec = {
                "tracks": [
                    {
                        "reference": self.references[t["track"]],
                        "start_sec": t["start_sec"],
                        "end_sec": t["end_sec"],
                    }
                    for t in plan["tracks"]
                ],
                "crossfades": list(plan["crossfades"]),
            }
            again = checked(spec, self.references, self.facts())
        except (MixPlanRefused, IndexError, KeyError, TypeError):
            raise JobFailure("invalid_output") from None
        if again != spec:  # the stage's plan was snapped already: nothing may move here
            raise JobFailure("invalid_output")
        return again


def build(
    studio: StudioResult,
    outcome: StageOutcome,
    music: StudioMusic,
    song_list_id: UUID,
    *,
    origin: Origin = "design_draft",
    base_revision_id: UUID | None = None,
    base_hash: bytes | None = None,
    request_message_id: UUID | None = None,
    rules: Mapping[str, Any] | None = None,
) -> DraftRevision:
    """The studio draft to store; ``invalid_output`` if it no longer validates or changes
    nothing."""
    draft = revision.fitted_draft(studio.result)
    try:
        workout.validate(draft.to_spec())
        spec = MixSpec.model_validate(music.spec_of(studio.composed.plan))
        stored = StudioRevisionMusic(
            source=SOURCE,
            song_list_id=song_list_id,
            mix_spec=spec,
            map_version=int(studio.composed.map["version"]),
            analyses=list(music.records),
        )
    except (ValueError, ValidationError):
        raise JobFailure("invalid_output") from None
    content = draft.model_dump(mode="json", exclude_none=True)
    if len(revision.canonical(content)) > revision.MAX_CONTENT_BYTES:
        raise JobFailure("invalid_output")
    dumped = stored.model_dump(mode="json")
    if studio.overlay is not None and not isinstance(studio.overlay, EditOverlay):
        raise JobFailure("internal")
    overlay = None if studio.overlay is None else studio.overlay.stored()  # ADR 0042 section 3
    content_hash = revision_hash(content, mix_spec=dumped["mix_spec"], overlay=overlay)
    if base_hash is not None and content_hash == base_hash:
        raise JobFailure("invalid_output")  # the base again: not a revision
    made = provenance(
        [outcome],
        studio.composed.map,
        origin=origin,
        base_revision_id=base_revision_id,
        request_message_id=request_message_id,
        tss_approximate=studio.result.tss_approximate,
        rules=rules,
    )
    return DraftRevision(
        content=content,
        content_hash=content_hash,
        music=dumped,
        provenance=made.model_dump(mode="json"),
        parent_id=base_revision_id,
        edit_overlay=overlay,
    )
