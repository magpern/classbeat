"""Gate and check hardening from the EV4 review (#269), for M3c T4 (#219).

- a ``pool_too_small`` run on a case the manifest does not list is a failed run the gate counts
  as such (not only the inconsistent-row rule catches a row that claims a pass);
- the song check ``nothing_stored`` of a run that ended as expected can fail (suite version 7);
- the cost bounds of the two groups are reported under different names (documented in ADR 0012).
"""

import copy
from typing import Any

from advscripts import provider_for_case as adv_provider
from agentfake import CONFIG
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.evals import gate
from classbeat.evals.pick import load_pick_cases, run_pick_case
from classbeat.evals.runner import suite_results
from classbeat.evals.suite import load_suite
from pickscripts import script_for as smoke_script
from test_evals_suite_adversarial import LIMITS, MANIFEST, header, own_rows

SUITE = load_suite()


async def full_doc() -> dict[str, Any]:
    rows = [row for repeat in range(3) for row in await own_rows(repeat)]
    return suite_results(header(), rows)


def failed_pool_too_small(doc: dict[str, Any], case: str, repeats: set[int]) -> dict[str, Any]:
    """``doc`` with the given runs of ``case`` ended as ``pool_too_small`` and failed (a row that
    claims no pass, so it is consistent: the manifest does not expect that outcome from it)."""
    out = copy.deepcopy(doc)
    for row in out["results"]:
        if row["case"] == case and row["repeat"] in repeats:
            row["outcome"], row["passed"], row["checks"] = "pool_too_small", False, []
    return out


async def test_a_failed_pool_too_small_run_on_an_unlisted_case_is_not_a_pass() -> None:
    doc = await full_doc()
    base = gate.evaluate(doc, MANIFEST).groups["songs"]
    assert base.status == "passed"
    cost = base.categories["cost"]

    one = failed_pool_too_small(doc, "cost_pick_30", {0})
    assert not gate._file_rules(  # pyright: ignore[reportPrivateUsage]
        gate._Doc.model_validate(one),  # pyright: ignore[reportPrivateUsage]
        MANIFEST,
    )  # consistent: a failed row, not a row claiming a pass
    verdict = gate.evaluate(one, MANIFEST).groups["songs"]
    assert verdict.categories["cost"].runs == cost.runs
    assert verdict.categories["cost"].passes == cost.passes - 1  # not counted as a pass
    assert verdict.categories["cost"].rules_failed == ("pass_share",)
    assert verdict.status == "failed"
    # The one case the manifest does expect it from still counts as the pass it is.
    assert verdict.categories["missing_music"] == base.categories["missing_music"]

    every = failed_pool_too_small(doc, "cost_pick_30", {0, 1, 2})
    worst = gate.evaluate(every, MANIFEST).groups["songs"].categories["cost"]
    assert "each_case_once" in worst.rules_failed


async def test_the_expected_pool_too_small_is_still_a_pass_and_only_that_case() -> None:
    doc = await full_doc()
    expected = MANIFEST.expected_outcomes
    assert expected == {"mm_pool_too_small": "pool_too_small"}
    songs = gate.evaluate(doc, MANIFEST).groups["songs"]
    assert songs.categories["missing_music"].passes == songs.categories["missing_music"].runs


# --- nothing_stored ----------------------------------------------------------------------------


async def test_nothing_stored_passes_for_a_pool_that_ended_before_the_choose_stage() -> None:
    case = SUITE.song_cases["mm_pool_too_small"]
    result, _, _ = await run_pick_case(case, adv_provider(case), CONFIG, LIMITS)
    check = next(c for c in result.checks if c.name == "nothing_stored")
    assert check.ok
    assert check.detail == "no list, no choose stage"


async def test_nothing_stored_fails_when_the_choose_stage_began() -> None:
    """A run that ends as the case expects, but only after the choose stage began: the check can
    fail (before suite version 7 it was ``chosen is None``, true on every failure path)."""
    case = next(c for c in load_pick_cases() if c.name == "pick_30")
    turns = smoke_script(case).model_dump(mode="json")["turns"]
    cut = FakeScript.model_validate(
        {"model": "fake-model", "turns": [*turns[:3], {"error": "auth"}]}
    )
    expecting = case.model_copy(
        update={"expect": case.expect.model_copy(update={"outcome": "model_config"})}
    )
    result, _, _ = await run_pick_case(expecting, FakeProvider(cut), CONFIG, LIMITS)
    assert result.outcome == "model_config"
    assert [c.name for c in result.checks] == ["expected_outcome", "nothing_stored"]
    assert result.failed_checks == ["nothing_stored"]
    assert not result.passed
