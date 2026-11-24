"""The ``ride_shape`` eval check (#1493): a design case with a ride shape in the suite
(``soundless_ride_shape``), judged by ``agent.shape_check`` on its replayed fake provider. A draft
that skips, reorders, miscounts or leaves a section untagged fails the named check. No model, no
network."""

import copy
import json
from typing import Any

import pytest

from agentfake import CONFIG
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.soundless import check_soundless
from classbeat.evals.cases import Case
from classbeat.evals.runner import run_case
from classbeat.evals.shape_checks import follows_shape
from classbeat.evals.suite import load_suite
from soundlessscripts import good_draft, provider_for_case

SUITE = load_suite()
CASE = SUITE.design_cases["soundless_ride_shape"]


def judged(draft: dict[str, Any]) -> tuple[bool, str]:
    result = check_soundless(WorkoutDraft.model_validate(draft))
    return follows_shape(CASE.brief, result.draft, result.spec)


def test_the_case_is_in_the_suite_with_a_shape_and_the_check() -> None:
    assert SUITE.manifest.case_index()["soundless_ride_shape"] == ("soundless", "design")
    assert CASE.soundless
    assert CASE.expect.ride_shape
    assert [s.kind for s in CASE.brief.shape] == [
        "warmup", "climb", "efforts", "flat", "climb", "efforts",
    ]  # fmt: skip


async def test_the_recorded_run_passes_every_check_including_the_ride_shape() -> None:
    result = await run_case(
        CASE,
        provider_for_case(CASE),
        CONFIG,
        zone7_tss_margin=SUITE.manifest.zone7_tss_margin,
    )
    assert result.passed, [c.detail for c in result.checks if not c.ok]
    assert "ride_shape" in [c.name for c in result.checks]


def test_a_ride_shape_check_is_for_a_case_whose_brief_has_a_shape() -> None:
    data = CASE.model_dump(mode="json", exclude_none=True)
    data["brief"] = {"length_min": 30}
    with pytest.raises(ValueError, match="shape"):
        Case.model_validate_json(json.dumps(data))


def efforts(draft: dict[str, Any], section: int) -> list[dict[str, Any]]:
    (seg,) = [s for s in draft["segments"] if s.get("section") == section]
    return seg["intervals"]


def test_the_good_draft_follows_the_shape() -> None:
    assert judged(good_draft(CASE))[0]


def test_an_effort_too_many_or_too_few_fails() -> None:
    more = good_draft(CASE)
    efforts(more, 5).extend(copy.deepcopy(efforts(more, 5)[:2]))  # 3 + 1 = 4 is the range's end
    assert judged(more)[0]
    over = good_draft(CASE)
    efforts(over, 5).extend(copy.deepcopy(efforts(over, 5)[:4]))  # 5 efforts, the range's end is 4
    ok, detail = judged(over)
    assert not ok
    assert "shape.5 shape_count" in detail
    short = good_draft(CASE)
    del efforts(short, 2)[8:]  # 4 efforts, the range starts at 5
    assert "shape.2 shape_count" in judged(short)[1]


def test_a_section_left_out_an_order_swapped_and_an_untagged_draft_fail() -> None:
    left_out = good_draft(CASE)
    left_out["segments"] = [s for s in left_out["segments"] if s.get("section") != 3]
    assert "shape.3 shape_missing" in judged(left_out)[1]
    swapped = good_draft(CASE)
    segs = swapped["segments"]
    first, second = [i for i, s in enumerate(segs) if s.get("section") in (2, 3)][:2]
    segs[first], segs[second] = segs[second], segs[first]
    assert not judged(swapped)[0]
    untagged = good_draft(CASE)
    for s in untagged["segments"]:
        s.pop("section", None)
    ok, detail = judged(untagged)
    assert not ok
    assert "shape_untagged" in detail
