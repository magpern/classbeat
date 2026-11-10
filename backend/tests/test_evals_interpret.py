"""The suite's interpret category (ADR 0029 section 4, issue #880): the case model, the checks and
the runner, on recorded scripts. No model, no network."""

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import CONFIG, from_dict
from classbeat.agent.interpret import FINAL_TOOL
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.evals import suite_live
from classbeat.evals.interpret_cases import InterpretCase
from classbeat.evals.interpret_checks import check_interpretation
from classbeat.evals.interpret_run import run_interpret_case
from classbeat.evals.suite import load_suite
from interpretscripts import provider_for_case, submission

SUITE = load_suite()
CASES = SUITE.interpret_cases


def test_the_suite_holds_the_eight_interpret_cases_in_their_own_category() -> None:
    own = SUITE.manifest.cases_of("interpret", "design")
    assert sorted(CASES) == sorted([*own, "interpret_playlist_injection"])
    assert {SUITE.category_of(n) for n in own} == {"interpret"}
    assert SUITE.category_of("interpret_playlist_injection") == "adversarial"
    covered = " ".join(c.request for c in CASES.values()).lower()
    for word in ("power pull", "game", "run", "ett hårt", "et roligt", "playlist"):
        assert word in covered


@pytest.mark.parametrize("name", sorted(CASES))
async def test_a_good_submission_passes_every_check(name: str) -> None:
    result = await run_interpret_case(CASES[name], provider_for_case(CASES[name]), CONFIG)
    assert result.passed, [(c.name, c.detail) for c in result.checks if not c.ok]
    assert [c.name for c in result.checks][-1] == "limits"
    assert "forbidden_text" in [c.name for c in result.checks]


def _with(case: InterpretCase, mutate: Any) -> dict[str, Any]:
    data = copy.deepcopy(submission(case))
    mutate(data)
    return data


async def _failed(case: InterpretCase, data: dict[str, Any]) -> list[str]:
    script = {"turns": [{"calls": [{"name": FINAL_TOOL, "input": data}]}]}
    return (await run_interpret_case(case, from_dict(script), CONFIG)).failed_checks


async def test_a_wrong_missing_or_extra_value_fails_the_check_that_names_it() -> None:
    case = CASES["interpret_explicit_kept"]
    assert await _failed(case, _with(case, lambda d: d["length_min"].update(value=30))) == [
        "values"
    ]
    assert await _failed(case, _with(case, lambda d: d.pop("climbs"))) == ["values"]
    assert await _failed(case, _with(case, lambda d: d["climbs"].update(origin="inferred"))) == [
        "values"
    ]
    extra = _with(case, lambda d: d.update(races={"value": 1, "origin": "inferred"}))
    assert await _failed(case, extra) == ["absent"]


async def test_the_games_are_judged_in_order_and_in_their_closed_form() -> None:
    case = CASES["interpret_game_type"]
    assert await _failed(case, _with(case, lambda d: d.pop("games"))) == ["games"]
    wrong = _with(case, lambda d: d["games"][0].update(type="overflow"))
    assert await _failed(case, wrong) == ["games"]
    moved = _with(case, lambda d: d["games"][0]["placement"].update(ordinal=1))
    assert await _failed(case, moved) == ["games"]
    no_games = CASES["interpret_playlist_injection"]
    extra = _with(no_games, lambda d: d.update(games=[{"type": "any", "origin": "inferred"}]))
    assert await _failed(no_games, extra) == ["games"]


async def test_an_injection_that_is_obeyed_fails_the_case() -> None:
    case = CASES["interpret_playlist_injection"]
    obeyed = _with(case, lambda d: d["length_min"].update(value=180))
    assert await _failed(case, obeyed) == ["values"]
    # An injected sport is a valid closed value, so the case must catch it by value.
    sport = _with(case, lambda d: d.update(sport={"value": "running", "origin": "inferred"}))
    assert await _failed(case, sport) == ["values"]
    # Text the model invents never survives the stage: its quote is dropped, the run passes ...
    said = _with(case, lambda d: d["length_min"].update(quote="PWNED"))
    assert await _failed(case, said) == []
    # ... and were it kept, the checks would catch it (an unsanitised request, as a mutation).
    kept = InterpretedRequest.model_validate(said)
    assert [c.name for c in check_interpretation(case, kept) if not c.ok] == [
        "quotes",
        "forbidden_text",
    ]


async def test_a_run_that_never_submits_is_a_failed_outcome_not_a_pass() -> None:
    case = CASES["interpret_swedish"]
    script = {"turns": [{"end_turn": "I will not."}]}
    result = await run_interpret_case(case, from_dict(script), CONFIG)
    assert not result.passed
    assert result.outcome != "submitted"


def test_the_case_model_refuses_what_it_cannot_judge() -> None:
    base = json.loads(CASES["interpret_explicit_kept"].model_dump_json())
    for edit in (
        lambda d: d["expect"]["values"].update(games=1),  # games have their own field
        lambda d: d["expect"]["absent"].append("length_min"),  # set and absent
        lambda d: d["expect"]["origins"].update(races="explicit"),  # origin of nothing expected
        lambda d: d["expect"]["values"].update(music=0),  # an index into no names
        lambda d: d.update(extra=1),
    ):
        data = copy.deepcopy(base)
        edit(data)
        with pytest.raises(ValidationError):
            InterpretCase.model_validate_json(json.dumps(data))


def test_the_live_run_order_includes_the_interpret_cases_as_design_group_runs() -> None:
    runs = suite_live.run_order(SUITE, 1)
    mine = [r for r in runs if r.case in CASES]
    assert len(mine) == len(CASES)
    assert {r.group for r in mine} == {"design"}


def test_the_injection_case_is_judged_in_adversarial_where_every_run_must_pass() -> None:
    from classbeat.evals.suite import load_suite

    assert "interpret_playlist_injection" in SUITE.manifest.cases_of("adversarial", "design")
    assert "adversarial" in SUITE.manifest.rules.every_run_categories
    assert load_suite().interpret_cases["interpret_playlist_injection"].expect.not_values
