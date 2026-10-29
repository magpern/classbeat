"""``python -m classbeat.evals smoke --live``: the owner's live run of the smoke set (ADR 0006
section 8). Not run by CI or by an agent; it spends the owner's money.

It cannot run by accident. It refuses (exit 2, one line, no values) unless **all** hold:

1. ``--live`` is passed (without it the command only says where the offline set runs);
2. ``CI`` is not set;
3. ``CLASSBEAT_MODEL_PROVIDER`` is ``anthropic``;
4. the ``anthropic_api_key`` secret file holds a key, and the SDK environment is clean
   (``check_worker_model_provider``);
5. the model is priced (``classbeat.agent.prices``);
6. ``--max-usd N`` is passed, after the command has printed its plan: the cases, the limits of ADR
   0006 section 5 and the **cost ceiling**. Without it nothing is sent.

The ceiling is the worst case: every case may spend the per-job ceiling plus one turn's over-run
(``max_tokens`` minus the output reserve, section 5), or ``--max-usd`` plus one over-run when that
is lower: each case runs with the money left as its job ceiling, and the run stops once nothing
is left. Cases run one after the other with one provider each; the worker's outbound allowlist
(``api.anthropic.com`` only in effect) is installed before the provider is built. The key is
never printed; failures print their category or exception class only. Results (a table, and
JSON with ``--out``) hold per case the outcome, turns, cost, prompt version and failed check
names: no model text, no ids, no secrets. Synthetic music only: no user account is touched.
"""

import asyncio
import logging
import os
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TextIO

from classbeat.agent.limits import Limits
from classbeat.agent.loop import AgentConfig
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers import ProviderUnavailable, provider_for
from classbeat.config import ModelProviderRefused, Settings, check_worker_model_provider
from classbeat.egress import install_worker_allowlist
from classbeat.evals.cases import Case, load_cases
from classbeat.evals.runner import CaseResult, results_json, results_table, run_case

logger = logging.getLogger(__name__)
REFUSED = 2
MAX_USD_LIMIT = Decimal(100)
MICROS = Decimal(1_000_000)


class LiveRefused(Exception):
    """The live run may not start; the message names the rule only."""


def usd(micros: int) -> str:
    return f"${Decimal(micros) / MICROS:.2f}"


def case_ceiling_micros(config: AgentConfig) -> int:
    """Worst case of one case: the job ceiling plus one turn's over-run (ADR 0006 section 5)."""
    price = config.prices.lookup("anthropic", config.model)
    if price is None:
        raise LiveRefused(f"the model {config.model} has no price in classbeat.agent.prices")
    limits = config.limits
    over = price.output_micros(max(config.max_tokens - limits.turn_output_reserve, 0))
    return limits.job_cost_micros + over


def run_ceiling_micros(config: AgentConfig, cases: int, budget_micros: int | None) -> int:
    each = case_ceiling_micros(config)
    full = cases * each
    if budget_micros is None:
        return full
    return min(full, budget_micros + each - config.limits.job_cost_micros)


def check_preconditions(settings: Settings, args_live: bool) -> None:
    if not args_live:
        raise LiveRefused(
            "pass --live to run the smoke set against the real model; "
            "the offline set runs in the backend tests (tests/test_evals_smoke.py)"
        )
    if os.environ.get("CI"):
        raise LiveRefused("refusing to run live when CI is set")
    if settings.model_provider != "anthropic":
        raise LiveRefused("refusing: CLASSBEAT_MODEL_PROVIDER is not anthropic")
    try:
        check_worker_model_provider(settings)
    except ModelProviderRefused as refused:
        raise LiveRefused(f"refusing: {refused}") from None


def select(names: Sequence[str] | None) -> list[Case]:
    cases = load_cases()
    if not names:
        return cases
    known = {c.name for c in cases}
    unknown = sorted(set(names) - known)
    if unknown:
        raise LiveRefused(f"unknown case: {', '.join(unknown)}")
    return [c for c in cases if c.name in names]


def plan_text(config: AgentConfig, cases: Sequence[Case], budget_micros: int | None) -> str:
    limits = config.limits
    ceiling = run_ceiling_micros(config, len(cases), budget_micros)
    return "\n".join(
        [
            f"classbeat smoke set, LIVE: {len(cases)} cases, model {config.model}, "
            f"effort {config.effort}",
            f"limits per case (ADR 0006 section 5): {limits.max_turns} turns, "
            f"{limits.max_tool_calls} tool calls, {limits.max_corrections} corrections, "
            f"{limits.stage_seconds:.0f} s, {usd(limits.job_cost_micros)} job ceiling",
            f"estimated cost ceiling for this run: {usd(ceiling)} "
            f"(worst case; the budget is {usd(budget_micros) if budget_micros else 'not set'})",
            "synthetic music only; no user account is touched",
        ]
    )


def with_job_ceiling(config: AgentConfig, micros: int) -> AgentConfig:
    """The config with another per-job cost ceiling; everything else as it was."""
    lim = config.limits
    limits = Limits(
        lim.max_turns,
        lim.max_tool_calls,
        lim.max_corrections,
        lim.stage_seconds,
        micros,
        lim.turn_output_reserve,
    )
    return AgentConfig(config.model, config.effort, config.max_tokens, limits, config.prices)


async def run_live(
    cases: Sequence[Case],
    provider_factory: Callable[[], ModelProvider],
    config: AgentConfig,
    budget_micros: int,
    out: TextIO,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    left = budget_micros
    for case in cases:
        if left <= 0:
            print(f"budget used up; {case.name} and the rest not run", file=out)
            break
        limited = with_job_ceiling(config, min(config.limits.job_cost_micros, left))
        try:
            result = await run_case(case, provider_factory(), limited)
        except Exception as exc:  # the class name only: a message could hold request data
            logger.error(
                "smoke case failed", extra={"case": case.name, "error": type(exc).__name__}
            )
            result = CaseResult(case.name, f"error:{type(exc).__name__}")
        left -= result.cost_micros
        results.append(result)
        print(f"{case.name}: {'pass' if result.passed else 'FAIL'} ({result.outcome})", file=out)
    return results


def main_live(
    settings: Settings,
    *,
    live: bool,
    max_usd: str | None,
    case_names: Sequence[str] | None,
    out_path: Path | None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    provider_factory: Callable[[Settings], ModelProvider] = provider_for,
    install: Callable[[], object] = install_worker_allowlist,
) -> int:
    try:
        check_preconditions(settings, live)
        config = AgentConfig.from_settings(settings)
        cases = select(case_names)
        budget: int | None = None
        if max_usd is not None:
            try:
                amount = Decimal(max_usd)
            except ArithmeticError:
                amount = Decimal(0)
            if not (amount.is_finite() and 0 < amount <= MAX_USD_LIMIT):
                raise LiveRefused(f"--max-usd must be above 0 and at most {MAX_USD_LIMIT}")
            budget = int(amount * MICROS)
        print(plan_text(config, cases, budget), file=out)
        if budget is None:
            raise LiveRefused("not run: pass --max-usd N to accept spending up to N dollars")
        install()  # before the provider is built: every request goes through the allowlist
        provider_factory(settings)  # builds once to fail early on a missing key; discarded
    except LiveRefused as refused:
        print(str(refused), file=err)
        return REFUSED
    except ProviderUnavailable as unavailable:
        print(f"refusing: {unavailable}", file=err)
        return REFUSED
    results = asyncio.run(run_live(cases, lambda: provider_factory(settings), config, budget, out))
    print(results_table(results), file=out)
    if out_path is not None:
        out_path.write_text(results_json(results), encoding="utf-8")
    return 0 if results and all(r.passed for r in results) else 1
