"""#1328 (#580's approved design, ADR 0040 sections 3 and 5): the cut candidates of an
``analysis-v1`` document. Every kind is read on the file clock, trust is inherited (the feature's
``feature_trust_policy`` verdict **and** an ``exact`` track), the clock is mapped as
``reference_time = file_time + offset_s`` and any doubt gives no candidates. Pure: no I/O."""

import copy
from typing import Any

import pytest

from classbeat.spotdown import cut_candidates_calc as calc
from classbeat.spotdown.cut_candidates_model import KINDS, Candidate, as_json, parse
from classbeat.spotdown.timing_class_policy import TimingDecision
from spotdownfake import example

EXACT = TimingDecision("exact", "played_recording", {})
APPROXIMATE = TimingDecision("approximate", "matched_recording", {"offset_s": -0.4})


def doc() -> dict[str, Any]:
    return copy.deepcopy(example("analysis-v1.full.json"))


def by_kind(found: tuple[Candidate, ...] | list[Candidate]) -> dict[str, list[Candidate]]:
    out: dict[str, list[Candidate]] = {}
    for c in found:
        out.setdefault(c.kind, []).append(c)
    return out


def test_every_kind_of_the_full_example_is_read_on_the_file_clock() -> None:
    found = by_kind(calc.points(doc()))
    assert [c.at_s for c in found["content_start"]] == [0.4]
    assert [c.at_s for c in found["content_end"]] == [11.5]
    assert [(c.at_s) for c in found["silence_start"] + found["silence_end"]] == [0.0, 0.4]
    assert [c.at_s for c in found["fade_out_start"] + found["fade_out_end"]] == [10.0, 11.5]
    assert "fade_in_start" not in found  # fade_in is null in the example
    assert [c.at_s for c in found["segment_start"]] == [0.4, 6.0]
    assert [(c.at_s, c.label) for c in found["label_start"]] == [(0.4, "intro")]
    assert [c.at_s for c in found["phrase_start"] + found["phrase_end"]] == [0.4, 7.9]
    assert [c.at_s for c in found["downbeat"]] == [0.4]
    assert set(found) <= KINDS


def test_trust_is_the_features_own_on_the_file_clock() -> None:
    found = by_kind(calc.points(doc()))
    assert found["content_start"][0].trusted  # signal: measured 0.95
    assert found["segment_start"][0].trusted  # estimated, boundary_confidence 0.8
    assert found["downbeat"][0].trusted  # estimated 0.6
    assert not found["label_start"][0].trusted  # labels are heuristic
    assert not found["phrase_start"][0].trusted  # heuristic 0.4
    assert found["label_start"][0].method == "heuristic"


@pytest.mark.parametrize(
    ("method", "confidence"),
    [("heuristic", 0.99), ("estimated", 0.49), ("measured", None), ("guessed", 0.9), (None, 0.9)],
)
def test_a_heuristic_or_low_confidence_feature_is_never_trusted(
    method: str | None, confidence: float | None
) -> None:
    d = doc()
    d["structure"]["segments"][1] |= {"method": method, "boundary_confidence": confidence}
    d["grid"]["downbeats"] |= {"method": method, "confidence": confidence}
    found = by_kind(calc.candidates(d, EXACT))
    assert not next(c for c in found["segment_start"] if c.at_s == 6.0).trusted
    assert not found["downbeat"][0].trusted


def test_an_exact_track_inherits_the_features_trust_with_no_offset() -> None:
    found = calc.candidates(doc(), EXACT)
    trusted = {(c.kind, c.at_s) for c in found if c.trusted}
    assert {("content_start", 0.4), ("segment_start", 6.0)} <= trusted
    assert not {("label_start", 0.4), ("phrase_start", 0.4)} & trusted
    assert [c.at_s for c in found] == sorted(c.at_s for c in found)  # time order


def test_an_approximate_track_is_advisory_only_and_mapped_by_its_offset() -> None:
    found = calc.candidates(doc(), APPROXIMATE)
    assert found
    assert not any(c.trusted for c in found)
    kinds = by_kind(found)
    # reference_time = file_time + offset_s (-0.4): 0.4 -> 0.0, 6.0 -> 5.6
    assert [c.at_s for c in kinds["segment_start"]] == [0.0, 5.6]
    assert "silence_start" not in kinds  # 0.0 - 0.4 is before the reference starts
    assert all(0.0 <= c.at_s <= 11.9 for c in found)  # within the reference's 11.9 s


@pytest.mark.parametrize(
    "decision",
    [
        TimingDecision("unusable", "residual_too_large", {}),
        TimingDecision("unusable", "anchors_disagree", {}),
        TimingDecision("exact", "beat_equivalence", {}),  # v1 carries no offset for path (b)
    ],
)
def test_an_unusable_track_or_an_unmapped_clock_gives_no_candidates(
    decision: TimingDecision,
) -> None:
    assert calc.candidates(doc(), decision) == ()


@pytest.mark.parametrize(
    "spoil",
    [
        lambda d: d.pop("alignment"),
        lambda d: d["alignment"].pop("offset_s"),
        lambda d: d["alignment"].update(offset_s="0.4"),
        lambda d: d["alignment"].update(offset_s=float("nan")),
        lambda d: d["alignment"].update(offset_s=7200.0),
        lambda d: d["alignment"]["reference"].pop("duration_s"),
    ],
)
def test_any_doubt_about_the_approximate_mapping_refuses_it(spoil: Any) -> None:
    d = doc()
    spoil(d)
    assert calc.candidates(d, APPROXIMATE) == ()


def test_an_offset_other_than_the_decisions_evidence_refuses_the_mapping() -> None:
    other = TimingDecision("approximate", "matched_recording", {"offset_s": 0.2})
    assert calc.candidates(doc(), other) == ()
    assert calc.candidates(doc(), TimingDecision("approximate", "matched_recording", {}))


def test_an_exact_track_without_a_duration_refuses_the_mapping() -> None:
    d = doc()
    d["signal"].pop("duration_s")
    assert calc.candidates(d, EXACT) == ()


def test_points_past_the_played_track_are_left_out() -> None:
    d = doc()
    d["grid"]["downbeats"]["times_s"] = [0.4, 11.99, 12.5]
    found = by_kind(calc.candidates(d, EXACT))
    assert [c.at_s for c in found["downbeat"]] == [0.4, 11.99]  # 12.0 s long


def test_malformed_values_read_as_absent() -> None:
    d = doc()
    d["signal"]["content_start_s"] = True
    d["signal"]["silence_regions"] = [{"start_s": -1.0, "end_s": "x"}, "nope", {"end_s": 0.3}]
    d["structure"]["segments"][0]["start_s"] = None
    d["labels"] = [{"segment_id": ["s1"], "label": "intro"}, {"segment_id": "s2", "label": "evil"}]
    d["grid"]["downbeats"]["times_s"] = [0.4, "1.0"]
    found = by_kind(calc.points(d))
    assert "content_start" not in found
    assert [c.at_s for c in found["silence_end"]] == [0.3]
    assert [c.at_s for c in found["segment_start"]] == [6.0]
    assert [(c.at_s, c.label) for c in found["label_start"]] == [(6.0, None)]  # not a schema word
    assert "downbeat" not in found  # a list with a non-number is no list


def test_more_than_max_candidates_refuses_them_all() -> None:
    d = doc()
    d["grid"]["downbeats"]["times_s"] = [round(n * 0.001, 3) for n in range(calc.MAX_CANDIDATES)]
    assert calc.candidates(d, EXACT) == ()


def test_the_stored_shape_reads_back_whole_or_not_at_all() -> None:
    found = calc.candidates(doc(), EXACT)
    assert parse(as_json(found)) == found
    assert parse([]) == ()
    stored = as_json(found)
    for bad in (
        {**stored[0], "kind": "drop"},
        {**stored[0], "trusted": 1},
        {**stored[0], "at_s": -1.0},
        {**stored[0], "method": "guessed"},
        {**stored[0], "label": "evil"},
        {**stored[0], "confidence": 2.0},
        "point",
    ):
        assert parse([*stored, bad]) is None
    assert parse(None) is None
    assert parse({"at_s": 1.0}) is None
