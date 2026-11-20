"""A SwedeBeat mix's map from spotdown (ADR 0040 sections 3, 5 and 6; plan sections 2 and 4a;
SD-P7a, #1304). Pure: no I/O.

``mix_map_of`` reads spotdown's ``analysis-v1`` of the bytes classbeat fetched for the played mix
and **always goes through the policies**: the timing class from ``timing_class_policy`` on path
(a) (the analysed file is the mix that is played: an ``upload`` of the same sha256, uploaded as
that mix; a document of other bytes is never ``exact``), then the map from
``mix_map_policy.mix_map``. Only an ``exact`` ``played_recording`` mix that maps is used; anything
else is ``MixRefused`` with a code, and the caller analyses the mix locally.

The provenance kept with the map is ``provenance_model``'s record (the analysis id, versions, the
audio identity, the timing class with its reason and evidence, the policy versions, plus
``mix_map_policy``'s), at most ``PROVENANCE_MAX`` as the table measures it; ``source.ref`` is
always ``None`` (an upload has none). No title, file name, URL, link, scope or client ref; the
mix id is used only for the path (a) check and is not part of the record.

``current_mix_provenance`` is the cache's rule for a stored ``spotdown`` mix row: the record of
this version, ``exact`` on path (a), made under the policy versions of this build. Any other row
is analysed again.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.jsonb_text_calc import jsonb_text_size
from classbeat.spotdown import mix_map_policy, timing_class_policy
from classbeat.spotdown.provenance_model import POLICY_VERSIONS, RECORD_VERSION, provenance_of
from classbeat.spotdown.provenance_read_calc import analysed
from classbeat.spotdown.spotdown_map_policy import MapRefused
from classbeat.spotdown.timing_inputs_model import Played, TimingInputs

PLAYED_KIND: Final = "swedebeat_mix"
PROVENANCE_MAX: Final = 65536  # the table's check on ``swedebeat_mix_maps.provenance``
MIX_POLICY_VERSIONS: Final = (
    *POLICY_VERSIONS,
    ("mix_map_policy", mix_map_policy.POLICY_VERSION),
)


@dataclass(frozen=True, slots=True)
class MixMap:
    music_map: dict[str, object]
    provenance: dict[str, object]


@dataclass(frozen=True, slots=True)
class MixRefused:
    reason: str


def mix_map_of(
    doc: Mapping[str, object], mix_id: str, sha256: str, map_version: int
) -> MixMap | MixRefused:
    """The map and provenance of ``doc`` for the played mix ``mix_id`` whose bytes are
    ``sha256``."""
    played = Played(PLAYED_KIND, mix_id, sha256)
    seen = analysed(doc, uploaded_as=(PLAYED_KIND, mix_id))
    if seen is None:
        return MixRefused("no_audio_identity")
    decision = timing_class_policy.decide(TimingInputs(played, seen))
    if decision.timing_class != "exact" or decision.reason != "played_recording":
        return MixRefused("not_played_recording")
    provenance = provenance_of(doc, decision)
    if provenance is None:
        return MixRefused("no_provenance")
    mapped = mix_map_policy.mix_map(doc, decision, map_version)
    if isinstance(mapped, MapRefused):
        return MixRefused(mapped.reason)
    record = provenance.record()
    record["source"] = {"kind": provenance.source_kind, "ref": None}
    record["policy_versions"] = dict(MIX_POLICY_VERSIONS)
    try:
        fits = jsonb_text_size(record) <= PROVENANCE_MAX
    except (TypeError, ValueError):
        fits = False
    return MixMap(mapped.music_map, record) if fits else MixRefused("provenance_too_large")


def _obj(value: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    inner = value.get(key)
    return inner if isinstance(inner, Mapping) else None  # pyright: ignore[reportUnknownVariableType]


def current_mix_provenance(provenance: object) -> bool:
    """Whether a stored ``spotdown`` mix row's record is one this build would have made."""
    if not isinstance(provenance, Mapping):
        return False
    record: Mapping[str, object] = provenance  # pyright: ignore[reportUnknownVariableType]
    timing, versions = _obj(record, "timing"), _obj(record, "policy_versions")
    return (
        record.get("record_version") == RECORD_VERSION
        and timing is not None
        and timing.get("class") == "exact"
        and timing.get("reason") == "played_recording"
        and versions is not None
        and dict(versions) == dict(MIX_POLICY_VERSIONS)
    )
