"""SD-P4 part 3 (#1301, ADR 0040 section 9): the shadow comparison metrics
(``classbeat.spotdown.shadow_compare_calc``), table-driven. No database, no network."""

import copy
import json
from typing import Any

import pytest

from classbeat.spotdown import shadow_compare_calc as sc
from spotdownfake import example


def local(
    bpm: float = 120.0, first: float = 0.25, duration: float = 12.0, **kw: Any
) -> dict[str, Any]:
    sections = kw.get("sections", [{"start": 0.0, "end": 6.0, "energy": 0.0},
                                   {"start": 6.0, "end": duration, "energy": 1.0}])  # fmt: skip
    return {
        "duration": duration,
        "songs": [{"start": 0.0, "end": duration, "bpm": bpm, "first_downbeat": first}],
        "events": [],
        "sections": sections,
        "track_version": 1,
    }


def doc(**changes: Any) -> dict[str, Any]:
    document = copy.deepcopy(example("analysis-v1.minimal.json"))
    period = 60.0 / 120.0
    document["tempo"]["bpm"] = 120.0
    document["grid"]["beats"]["times_s"] = [0.25 + k * period for k in range(24)]
    for path, value in changes.items():
        node = document
        *parents, last = path.split(".")
        for key in parents:
            node = node[key]
        node[last] = value
    return document


def test_identical_grids_agree_on_every_metric() -> None:
    m = sc.compare(local(), doc()).metrics()
    assert m["bpm_local"] == m["bpm_spotdown"] == 120.0
    assert (m["bpm_relative_error"], m["bpm_agree"], m["bpm_agree_octave"]) == (0.0, True, True)
    assert (m["beat_f_70ms"], m["beat_offset_median_ms"]) == (1.0, 0.0)
    assert (m["beats_local"], m["beats_spotdown"]) == (24, 24)
    assert (m["boundary_f_3s"], m["boundary_f_1_5s"]) == (1.0, 1.0)  # both cut at 6.0
    assert (m["boundaries_local"], m["boundaries_spotdown"]) == (1, 1)
    assert (m["duration_local_s"], m["duration_spotdown_s"], m["duration_delta_s"]) == (12, 12, 0)
    assert m["content_duration_s"] == 11.1
    assert m["downbeat_delta_s"] is None  # the minimal document has no downbeats


@pytest.mark.parametrize(
    ("spotdown", "agree", "octave"),
    [
        (122.0, True, True),  # 1.7 %
        (123.0, False, False),  # 2.5 %
        (60.0, False, True),  # half
        (240.0, False, True),  # double
        (180.0, False, False),  # 3/2 is no octave
    ],
)
def test_bpm_agreement_is_two_percent_and_octave_aware(
    spotdown: float, agree: bool, octave: bool
) -> None:
    m = sc.compare(local(), doc(**{"tempo.bpm": spotdown})).metrics()
    assert (m["bpm_agree"], m["bpm_agree_octave"]) == (agree, octave)


def test_an_untrusted_tempo_or_beats_compare_nothing() -> None:
    m = sc.compare(local(), doc(**{"tempo.method": "heuristic", "grid.beats.confidence": 0.1}))
    metrics = m.metrics()
    assert metrics["bpm_spotdown"] is None
    assert metrics["bpm_agree"] is None
    assert (metrics["beat_f_70ms"], metrics["beats_spotdown"]) == (None, 0)


def test_a_shifted_grid_counts_only_beats_within_70_ms() -> None:
    near = sc.compare(local(first=0.25 + 0.06), doc()).metrics()
    assert near["beat_f_70ms"] == pytest.approx(1.0, abs=0.05)
    assert near["beat_offset_median_ms"] == pytest.approx(-60.0)
    far = sc.compare(local(first=0.25 + 0.2), doc()).metrics()
    assert far["beat_f_70ms"] == 0.0
    assert far["beat_offset_median_ms"] is None


def test_boundaries_within_3_s_but_not_1_5_s() -> None:
    sections = [{"start": 0.0, "end": 8.0, "energy": 0.0}, {"start": 8.0, "end": 12.0, "energy": 1}]
    m = sc.compare(local(sections=sections), doc()).metrics()
    assert (m["boundary_f_3s"], m["boundary_f_1_5s"]) == (1.0, 0.0)


def test_downbeat_delta_is_spotdowns_first_minus_the_local_one() -> None:
    downbeats = {"times_s": [0.75, 2.75], "confidence": 0.8, "method": "estimated"}
    m = sc.compare(local(first=0.25), doc(**{"grid.downbeats": downbeats})).metrics()
    assert m["downbeat_delta_s"] == 0.5


@pytest.mark.parametrize(
    "broken",
    [{}, {"songs": []}, {"duration": "x", "songs": [{"bpm": True}]}, {"songs": [{}, {}]}],
)
def test_an_unreadable_local_map_reads_as_absent_never_raises(broken: dict[str, Any]) -> None:
    m = sc.compare(broken, doc()).metrics()
    assert m["bpm_local"] is None
    assert m["beats_local"] == 0
    assert m["beat_f_70ms"] is None


def test_the_metrics_are_numbers_booleans_and_none_under_fixed_names() -> None:
    m = sc.compare(local(), doc()).metrics()
    assert all(v is None or isinstance(v, bool | int | float) for v in m.values())
    assert json.dumps(m, allow_nan=False)  # finite


def test_matching_is_one_to_one() -> None:
    assert sc.matched_offsets([1.0], [0.98, 1.0, 1.02], 0.05) == [pytest.approx(-0.02)]
    assert len(sc.matched_offsets([1.0, 1.04], [1.02], 0.05)) == 1
    assert sc.f_measure(0, 3, 0) is None
    assert sc.f_measure(0, 3, 3) == 0.0


def test_too_many_points_compare_nothing() -> None:
    assert sc.local_grid(3600.0, 400.0, 0.0)  # 24000 beats: within the cap
    assert sc.local_grid(24 * 3600.0, 400.0, 0.0) == []
