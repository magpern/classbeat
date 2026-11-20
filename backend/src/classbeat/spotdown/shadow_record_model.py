"""What shadow mode records for one studio track (ADR 0040 sections 6 and 9): the outcome, the
comparison metrics and the provenance record. Pure: no I/O.

``evaluate`` reads spotdown's ``analysis-v1`` of the bytes classbeat uploaded for the played track
and decides its timing class with ``timing_class_policy`` on **path (a) only** (the analysed file
is the played recording: the same item, the same sha256). No recording match, alignment, beat
equivalence (path (b) has no consumer) or anchors are given, so a document of other bytes is never
``exact``. The played reference is used only for that check and is never part of the record.

**The record holds no text of the track**: the metrics are ``shadow_compare_calc``'s numbers plus
the timing class and reason, the analyser's name and version (codes, else ``other``) and whether
``spotdown_map_policy`` would map it (``map``: ``ok`` or the refusal's code). The provenance is
``provenance_model``'s record: ids, versions, the audio identity and the timing evidence, never a
title, a file name, a URL, a scope or a client ref.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from classbeat.spotdown import shadow_compare_calc, spotdown_map_policy, timing_class_policy
from classbeat.spotdown.provenance_model import provenance_of
from classbeat.spotdown.provenance_read_calc import analysed
from classbeat.spotdown.shadow_policy import Outcome, detail_code
from classbeat.spotdown.timing_inputs_model import Played, TimingInputs

PLAYED_KIND: Final = "swedebeat_track"
VERSION: Final = re.compile(r"[A-Za-z0-9._+-]{1,40}")

Metric = float | int | bool | str | None


@dataclass(frozen=True, slots=True)
class ShadowRecord:
    outcome: Outcome
    detail: str | None = None
    metrics: Mapping[str, Metric] | None = None
    provenance: Mapping[str, object] | None = None


def _version(value: object) -> str:
    return value if isinstance(value, str) and VERSION.fullmatch(value) else "other"


def evaluate(
    doc: Mapping[str, object],
    reference: str,
    sha256: str,
    local_map: Mapping[str, object],
    track_version: int,
) -> ShadowRecord:
    """The record of a comparison of ``doc`` with the local map of the same played track."""
    played = Played(PLAYED_KIND, reference, sha256)
    seen = analysed(doc, uploaded_as=(PLAYED_KIND, reference))
    if seen is None:
        return ShadowRecord("bad_document", "no_audio_identity")
    decision = timing_class_policy.decide(TimingInputs(played, seen))
    provenance = provenance_of(doc, decision)
    if provenance is None:
        return ShadowRecord("bad_document", "no_provenance")
    mapped = spotdown_map_policy.track_map(doc, decision, track_version)
    metrics: dict[str, Metric] = {
        **shadow_compare_calc.compare(local_map, doc).metrics(),
        "timing_class": decision.timing_class,
        "timing_reason": detail_code(decision.reason),
        "analyzer_name": _version(provenance.analyzer_name),
        "analyzer_version": _version(provenance.analyzer_version),
        "map": "ok" if isinstance(mapped, spotdown_map_policy.MappedTrack) else mapped.reason,
    }
    return ShadowRecord("compared", None, metrics, provenance.record())


def local_metrics(local_map: Mapping[str, object]) -> dict[str, Metric]:
    """The local side alone, kept from the relay on (a pending row)."""
    duration, bpm, _first = shadow_compare_calc.local_song(local_map)
    return {"bpm_local": bpm, "duration_local_s": duration}
