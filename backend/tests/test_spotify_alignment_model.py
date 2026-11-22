"""The pure rules of ``spotify_alignment_model`` (ADR 0042 sections 2 and 7.4, #1398): spotdown's
alignment fact is read, never changed; a correction is a separate number set against an analysis;
``listen_again`` unless that analysis is the one now."""

import math
from typing import Any

import pytest

from classbeat.classes import spotify_alignment_model as model
from classbeat.classes.spotify_alignment_model import Fact, SetAgainst

SHA = "ab" * 32
OTHER = "cd" * 32


def provenance(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"spotdown_analysis": {"v": 2, "songs": list(entries)}}


def entry(position: int = 0, **more: Any) -> dict[str, Any]:
    timing = more.pop(
        "timing", {"class": "approximate", "evidence": {"alignment": "ok", "offset_s": 1.5}}
    )
    return {
        "position": position, "analysis_id": "job-1", "audio": {"sha256": SHA}, "timing": timing
    } | more  # fmt: skip


def test_the_fact_is_read_from_the_record_by_position() -> None:
    facts = model.facts_of(provenance(entry(0), entry(2, analysis_id="job-3")))
    assert facts[0] == Fact(0, "job-1", SHA, "approximate", 1500)
    assert facts[2].analysis_id == "job-3"
    assert 1 not in facts


@pytest.mark.parametrize(
    "evidence",
    [
        {"alignment": "failed"},
        {"alignment": "ok"},
        {"alignment": "ok", "offset_s": "1"},
        {"alignment": "ok", "offset_s": True},
        {"alignment": "ok", "offset_s": math.inf},
        {"alignment": "ok", "offset_s": math.nan},
        {"alignment": "ok", "offset_s": 3601},
        {},
    ],
)
def test_an_alignment_that_is_not_ok_or_not_sane_has_no_offset(evidence: dict[str, Any]) -> None:
    fact = model.facts_of(provenance(entry(timing={"class": "unusable", "evidence": evidence})))[0]
    assert fact.offset_ms is None
    assert fact.timing_class == "unusable"


def test_the_offset_is_whole_milliseconds_and_may_be_negative_or_zero() -> None:
    for seconds, ms in ((-0.5, -500), (0.0, 0), (0.0004, 0), (2.3456, 2346)):
        timing = {"class": "exact", "evidence": {"alignment": "ok", "offset_s": seconds}}
        assert model.facts_of(provenance(entry(timing=timing)))[0].offset_ms == ms


def test_unknown_classes_and_malformed_ids_are_dropped() -> None:
    fact = model.facts_of(
        provenance(
            entry(
                analysis_id="", audio={"sha256": "nope"}, timing={"class": "great", "evidence": 3}
            )
        )
    )[0]
    assert (fact.analysis_id, fact.sha256, fact.timing_class, fact.offset_ms) == (None,) * 4


@pytest.mark.parametrize(
    "raw", [None, [], "x", {"spotdown_analysis": 1}, {"spotdown_analysis": {"songs": "x"}}]
)
def test_a_missing_or_malformed_record_has_no_facts(raw: Any) -> None:
    assert model.facts_of(raw if isinstance(raw, dict) else {}) == {}


def test_map_version_and_crossfade_come_from_the_draft_else_the_list() -> None:
    assert model.map_version_of({"map_version": 4}) == 4
    for bad in ({}, {"map_version": True}, {"map_version": "4"}, {"map_version": 4.0}):
        assert model.map_version_of(bad) is None
    assert model.crossfade_ms_of({"crossfade_sec": 8.5}, 9.0) == 8500
    assert model.crossfade_ms_of({"crossfade_sec": 0}, 9.0) == 0
    for bad in ({}, {"crossfade_sec": -1}, {"crossfade_sec": True}, {"crossfade_sec": "8"}):
        assert model.crossfade_ms_of(bad, 9.0) == 9000


def test_a_correction_is_set_against_the_analysed_file_and_the_map_now() -> None:
    fact = Fact(0, "job", SHA, "exact", 100)
    assert model.set_against(fact, 3) == SetAgainst(3, SHA)
    assert model.set_against(fact, None) == SetAgainst(None, SHA)
    assert model.set_against(None, 3) is None
    assert model.set_against(Fact(0, "job", None, "exact", 100), 3) is None
    assert SetAgainst(3, SHA).stored() == {"map_version": 3, "sha256": SHA}


def test_only_the_stored_shape_is_read_back() -> None:
    assert model.stored_set_against({"map_version": 3, "sha256": SHA}) == SetAgainst(3, SHA)
    assert model.stored_set_against({"map_version": None, "sha256": SHA}) == SetAgainst(None, SHA)
    for bad in (
        None,
        [],
        {},
        {"sha256": SHA, "map_version": "3"},
        {"sha256": SHA, "map_version": True},
        {"map_version": 3, "sha256": "x"},
    ):
        assert model.stored_set_against(bad) is None


def test_it_holds_only_against_exactly_the_analysis_now() -> None:
    now = SetAgainst(3, SHA)
    assert model.state_of(SetAgainst(3, SHA), now) == "current"
    assert model.state_of(SetAgainst(2, SHA), now) == "listen_again"  # another map
    assert model.state_of(SetAgainst(None, SHA), now) == "listen_again"
    assert model.state_of(SetAgainst(3, OTHER), now) == "listen_again"  # another file
    assert model.state_of(SetAgainst(3, SHA), None) == "listen_again"  # the analysis is gone
    assert model.state_of(None, now) == "listen_again"  # unreadable
    assert model.state_of(None, None) == "listen_again"


def test_an_offset_is_a_nonzero_whole_number_within_a_minute() -> None:
    assert [model.valid_offset(v) for v in (1, -1, 60_000, -60_000)] == [True] * 4
    assert [model.valid_offset(v) for v in (0, 60_001, -60_001, True)] == [False] * 4
