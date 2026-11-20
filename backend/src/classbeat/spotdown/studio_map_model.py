"""A studio track's map from spotdown (ADR 0040 sections 3, 5 and 6; plan sections 2 and 4a;
SD-P5, #1302). Pure: no I/O.

``studio_map`` reads spotdown's ``analysis-v1`` of the bytes classbeat downloaded for the played
SwedeBeat reference and **always goes through the two policies**: the timing class from
``timing_class_policy`` on path (a) (the analysed file is the played recording: an ``upload`` of
the same sha256; no match, alignment, beat equivalence or anchors are given, so a document of
other bytes is never ``exact``), then the track map from ``spotdown_map_policy.track_map``. Only
an ``exact`` ``played_recording`` track that maps is used; anything else is ``StudioRefused`` with
a code, and the caller analyses that track locally.

The provenance kept with the map is ``provenance_model``'s record (ids, versions, the audio
identity, the timing class with its reason and evidence, the policy versions), at most
``PROVENANCE_MAX`` as the table measures it; its ``source.ref`` is always ``None`` (an upload has
none). No title, file name, URL, scope or client ref; the played reference is used only for the
path (a) check and is not part of the record.

``current_provenance`` is the cache's rule for a stored ``spotdown`` row: the record of this
version, ``exact`` on path (a), made under the policy versions of this build. A row of other
policy versions is analysed again.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.jsonb_text_calc import jsonb_text_size
from classbeat.spotdown import spotdown_map_policy, timing_class_policy
from classbeat.spotdown.provenance_model import POLICY_VERSIONS, RECORD_VERSION, provenance_of
from classbeat.spotdown.provenance_read_calc import analysed
from classbeat.spotdown.timing_inputs_model import Played, TimingInputs

PLAYED_KIND: Final = "swedebeat_track"
PROVENANCE_MAX: Final = 65536  # the table's check on ``swedebeat_track_maps.provenance``


@dataclass(frozen=True, slots=True)
class StudioMap:
    track_map: dict[str, object]
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class StudioRefused:
    reason: str


def studio_map(
    doc: Mapping[str, object], reference: str, sha256: str, track_version: int
) -> StudioMap | StudioRefused:
    """The map and provenance of ``doc`` for the played ``reference`` whose bytes are ``sha256``."""
    played = Played(PLAYED_KIND, reference, sha256)
    seen = analysed(doc, uploaded_as=(PLAYED_KIND, reference))
    if seen is None:
        return StudioRefused("no_audio_identity")
    decision = timing_class_policy.decide(TimingInputs(played, seen))
    if decision.timing_class != "exact" or decision.reason != "played_recording":
        return StudioRefused("not_played_recording")
    provenance = provenance_of(doc, decision)
    if provenance is None:
        return StudioRefused("no_provenance")
    mapped = spotdown_map_policy.track_map(doc, decision, track_version)
    if isinstance(mapped, spotdown_map_policy.MapRefused):
        return StudioRefused(mapped.reason)
    record = provenance.record()
    record["source"] = {"kind": provenance.source_kind, "ref": None}
    try:
        fits = jsonb_text_size(record) <= PROVENANCE_MAX
    except (TypeError, ValueError):
        fits = False
    return StudioMap(mapped.track_map, record) if fits else StudioRefused("provenance_too_large")


def _obj(value: object, key: str) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    inner = value.get(key)  # pyright: ignore[reportUnknownMemberType]
    return inner if isinstance(inner, Mapping) else None  # pyright: ignore[reportUnknownVariableType]


def current_provenance(provenance: object) -> bool:
    """Whether a stored ``spotdown`` row's record is one this build would have made."""
    if not isinstance(provenance, Mapping):
        return False
    timing, versions = _obj(provenance, "timing"), _obj(provenance, "policy_versions")
    return (
        provenance.get("record_version") == RECORD_VERSION  # pyright: ignore[reportUnknownMemberType]
        and timing is not None
        and timing.get("class") == "exact"
        and timing.get("reason") == "played_recording"
        and versions is not None
        and dict(versions) == dict(POLICY_VERSIONS)
    )
