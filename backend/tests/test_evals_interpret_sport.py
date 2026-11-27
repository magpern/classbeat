"""The interpret suite's sport cases (#1544, suite version 35), replayed on recorded scripts: the
good submission passes, and the ``two_sports`` and ``sport_question`` checks fail what they must
(a request for both sports read as one, context read as both, the form's sport against the
request's that asks nothing). No model, no network."""

import copy
from typing import Any

import pytest

from agentfake import CONFIG, from_dict
from classbeat.agent.interpret import FINAL_TOOL
from classbeat.evals.interpret_cases import InterpretCase
from classbeat.evals.interpret_run import run_interpret_case
from classbeat.evals.suite import load_suite
from interpretscripts import submission

CASES = load_suite().interpret_cases
SPORT_CASES = (
    "interpret_sport_running",
    "interpret_two_sports",
    "interpret_sport_context",
    "interpret_sport_toggle",
)


async def failed(case: InterpretCase, data: dict[str, Any]) -> list[str]:
    script = {"turns": [{"calls": [{"name": FINAL_TOOL, "input": data}]}]}
    return (await run_interpret_case(case, from_dict(script), CONFIG)).failed_checks


@pytest.mark.parametrize("name", SPORT_CASES)
async def test_the_good_submission_passes(name: str) -> None:
    assert await failed(CASES[name], submission(CASES[name])) == []


def test_the_cases_are_the_issues_four() -> None:
    run, both, context, toggle = (CASES[n] for n in SPORT_CASES)
    assert (run.request, run.expect.values["sport"]) == ("A 45 min run with intervals", "running")
    assert (both.request, both.expect.two_sports) == ("A ride, then a 20 min run", True)
    assert (context.expect.values["sport"], context.expect.two_sports) == ("cycling", False)
    assert (toggle.sport_override, toggle.expect.sport_question) == ("running", True)
    assert "spin class" in toggle.request


async def test_both_sports_read_as_one_fail_two_sports() -> None:
    case = CASES["interpret_two_sports"]
    data = copy.deepcopy(submission(case))
    data.pop("two_sports")
    assert await failed(case, data) == ["two_sports"]


async def test_context_read_as_both_sports_fails_two_sports() -> None:
    case = CASES["interpret_sport_context"]
    data = {**copy.deepcopy(submission(case)), "two_sports": True}
    assert await failed(case, data) == ["two_sports"]


async def test_the_form_and_the_words_agreeing_fail_the_sport_question() -> None:
    """The toggle says running; a reading of running asks nothing, which the case refuses."""
    case = CASES["interpret_sport_toggle"]
    data = copy.deepcopy(submission(case))
    data["sport"] = {"value": "running", "origin": "inferred"}
    assert sorted(await failed(case, data)) == ["sport_question", "values"]
