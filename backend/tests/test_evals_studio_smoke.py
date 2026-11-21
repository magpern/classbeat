"""The studio's smoke case in CI (ADR 0036 section 4.4, SR C5): the workout and its mix through
the real studio stage against a recorded ``FakeProvider`` script and synthetic track analyses
(``classbeat.evals.studio``). No model, no network. Also: the studio checks fail on a bad mix."""

import json
from typing import Any

import pytest

from agentfake import CONFIG, from_dict, good_draft
from classbeat.agent.studio_mix import Composed
from classbeat.evals.cases import load_cases
from classbeat.evals.studio import load_studio_cases, run_studio_case, studio_checks, studio_tracks

CASES = load_studio_cases()


def mix_for(count: int) -> dict[str, Any]:
    tracks = studio_tracks(count)
    order = [1, 0, *range(2, count)]
    cuts = []
    for n in order:
        length = float(tracks[n].music_map["duration"])
        cuts.append({"track": n, "start_sec": 0, "end_sec": length})
    cuts[0]["start_sec"] = 8.0  # a cut that snaps to the first track's grid
    return {"tracks": cuts, "crossfades": [6, *([8] * (count - 2))]}


def script(count: int) -> dict[str, Any]:
    mix = mix_for(count)
    submit = {"draft": {"$draft": "good"}, "mix": mix}
    return {
        "model": "fake-model",
        "drafts": {"good": good_draft()},
        "turns": [
            {"calls": [{"name": "get_music", "input": {}, "expect": {"ok": True}}]},
            {"calls": [{"name": "get_music", "input": {"track": 0}, "expect": {"ok": True}}]},
            {"calls": [{"name": "preview_draft", "input": submit, "expect": {"ok": True}}]},
            {"calls": [{"name": "submit_draft", "input": submit}]},
        ],
    }


def test_the_studio_case_is_beside_the_thirty() -> None:
    assert [c.name for c in CASES] == ["studio_30"]
    assert len(load_cases()) == 30  # the design and revise cases are unchanged


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_the_case_passes_against_its_recorded_script(case: Any) -> None:
    fake = from_dict(script(case.music.tracks))

    def sent() -> str:
        return json.dumps([repr(s) for s in fake.sent], ensure_ascii=False)

    result = await run_studio_case(case, fake, CONFIG, sent)
    assert result.outcome == "submitted"
    assert result.passed, [(c.name, c.detail) for c in result.checks if not c.ok]
    assert {c.name for c in result.checks} >= {"mix_order", "mix_windows", "mix_total", "duration"}
    assert len(result.prompt_version) == 64
    assert fake.remaining == 0


def test_the_studio_checks_fail_on_a_bad_mix() -> None:
    plan = {
        "tracks": [{"track": 0, "start_sec": 0.0, "end_sec": 50.0}],  # one of two, 50 s played
        "crossfades": [],
    }
    composed = Composed(plan, {"duration": 49.0}, 50.0)
    failed = {c.name for c in studio_checks(composed, 2, "a ref-0123 was sent") if not c.ok}
    assert failed == {"mix_order", "mix_windows", "mix_total", "no_reference"}


def test_the_studio_checks_take_a_repeat_and_still_want_every_track() -> None:
    plan = {
        "tracks": [
            {"track": 0, "start_sec": 0.0, "end_sec": 60.0},
            {"track": 1, "start_sec": 0.0, "end_sec": 60.0},
            {"track": 0, "start_sec": 0.0, "end_sec": 60.0},  # #1342: a repeat
        ],
        "crossfades": [0, 0],
    }
    composed = Composed(plan, {"duration": 180.0}, 180.0)
    assert all(c.ok for c in studio_checks(composed, 2, "nothing"))
    failed = {c.name for c in studio_checks(composed, 3, "nothing") if not c.ok}
    assert failed == {"mix_order"}  # track 2 never plays
