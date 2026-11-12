"""The chained revise case (ADR 0029 section 4, #908): the request is read by the interpret stage,
layered onto the case's brief, and the revise stage gets the layered brief. On recorded scripts."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import CONFIG, from_dict
from classbeat.agent.interpret import FINAL_TOOL
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.evals.cases import Case
from classbeat.evals.interpret_checks import check_interpretation
from classbeat.evals.runner import run_case
from classbeat.evals.suite import load_suite
from suitescripts import script_for

SUITE = load_suite()
CHAIN = SUITE.design_cases["rev_chain_length_45_to_30"]
PLAIN = SUITE.design_cases["rev_stated_length_45_to_30"]
MARGIN = SUITE.manifest.zone7_tss_margin


def scripted(case: Case, reading: dict[str, Any] | None) -> Any:
    """The case's recorded script with its interpret turn replaced by ``reading`` (none: the
    model reads nothing, so the brief stays as it was)."""
    data = script_for(case).model_dump(mode="json", exclude_none=True)
    data["turns"][0]["calls"][0]["input"] = reading or {}
    return from_dict(data)


async def test_the_chain_reads_layers_and_revises_and_passes() -> None:
    result = await run_case(CHAIN, from_dict(script_dict(CHAIN)), CONFIG, zone7_tss_margin=MARGIN)
    assert result.passed, [(c.name, c.detail) for c in result.checks if not c.ok]


def script_dict(case: Case) -> dict[str, Any]:
    return script_for(case).model_dump(mode="json", exclude_none=True)


async def test_the_result_counts_both_runs() -> None:
    chained = await run_case(CHAIN, from_dict(script_dict(CHAIN)), CONFIG, zone7_tss_margin=MARGIN)
    plain = await run_case(PLAIN, from_dict(script_dict(PLAIN)), CONFIG, zone7_tss_margin=MARGIN)
    assert chained.turns == plain.turns + 1
    assert chained.cost_micros > plain.cost_micros
    assert chained.usage.input_tokens > plain.usage.input_tokens


async def test_a_reading_that_leaves_the_length_unchanged_fails_the_case() -> None:
    # Without the layered 30 the brief still states 45: the 30 minute class is refused.
    result = await run_case(CHAIN, scripted(CHAIN, None), CONFIG, zone7_tss_margin=MARGIN)
    assert not result.passed


async def test_a_failed_interpret_run_is_a_failed_case_that_still_counts_its_cost() -> None:
    script = {"turns": [{"end_turn": "I will not."}]}
    result = await run_case(CHAIN, from_dict(script), CONFIG, zone7_tss_margin=MARGIN)
    assert not result.passed
    assert result.outcome != "submitted"
    assert result.turns >= 1


def test_a_chained_case_revises_a_cycling_class_that_states_values() -> None:
    data = CHAIN.model_dump(mode="json")
    bad = copy.deepcopy(data)
    bad["brief"]["explicit"] = []
    with pytest.raises(ValidationError, match="chained"):
        Case.model_validate(bad)
    songs = copy.deepcopy(data)
    songs["revise"]["outcome"] = "songs_requested"
    with pytest.raises(ValidationError, match="chained"):
        Case.model_validate(songs)


def test_the_remove_games_case_is_judged_on_the_flag() -> None:
    case = SUITE.interpret_cases["interpret_revise_remove_games"]
    said = {"remove_games": True}
    ok = check_interpretation(case, InterpretedRequest.model_validate(said))
    assert all(c.ok for c in ok)
    silent = check_interpretation(case, InterpretedRequest.model_validate({}))
    assert [c.name for c in silent if not c.ok] == ["remove_games"]
    kept = InterpretedRequest.model_validate(
        {"games": [{"type": "overflow", "origin": "explicit"}]}
    )
    assert "games" in [c.name for c in check_interpretation(case, kept) if not c.ok]


def test_the_final_tool_name_is_the_one_the_script_uses() -> None:
    assert script_dict(CHAIN)["turns"][0]["calls"][0]["name"] == FINAL_TOOL
