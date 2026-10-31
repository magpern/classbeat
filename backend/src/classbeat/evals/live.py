"""``python -m classbeat.evals smoke --live``: the owner's live run of the smoke set (ADR 0006
section 8). Not run by CI or by an agent; it spends the owner's money.

It cannot run by accident. It refuses (exit 2, one line, no values) unless **all** hold:

1. ``--live`` is passed (without it the command only says where the offline set runs);
2. ``CI`` is not set;
3. ``CLASSBEAT_MODEL_PROVIDER`` is ``anthropic``;
4. the ``anthropic_api_key`` secret file holds a key, and the SDK environment is clean
   (``check_worker_model_provider``);
5. the model (``--model``, else ``CLASSBEAT_AGENT_MODEL``) is priced (``classbeat.agent.prices``:
   Opus, Sonnet and Haiku 5.5 today); ``--effort`` defaults to ``CLASSBEAT_AGENT_EFFORT`` and
   ``--repeat N`` runs each case N times (one round of all cases after the other, so a budget that
   runs out leaves every case with at least one run);
6. ``--max-usd N`` is passed, after the command has printed its plan: the cases, the limits of ADR
   0006 section 5 and the **cost ceiling**. Without it nothing is sent.

The ceiling is the worst case: every case may spend the per-job ceiling plus one turn's over-run
(``max_tokens`` minus the output reserve, section 5), or ``--max-usd`` plus one over-run when that
is lower: each case runs with the money left as its job ceiling, and the run stops once nothing
is left. Cases run one after the other with one provider each; the worker's outbound allowlist with
the run's model host (``install_model_allowlist``: ``api.anthropic.com``, ADR 0012 §2) is installed
before the provider is built. The key is
never printed; failures print their category or exception class only. Each failed check is printed
with its detail (numbers and rule names). Results (a table, and JSON with ``--out``) hold per run
the model, effort, prompt version and repeat index, the outcome, the metrics (turns, tool calls,
corrections, tokens, cost, wall time, TSS with its bounds, ``changes_on_music``), every check with
its detail and the shape of the class (``runner.class_shape``): no cues, no song titles, no ids, no
secrets. ``python -m classbeat.evals compare`` reads several of those files offline. Synthetic
music only: no user account is touched.
"""

import asyncio
import logging
import os
import re
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
from classbeat.evals.runner import (
    CaseResult,
    failed_check_lines,
    results_json,
    results_table,
    run_case,
)

logger = logging.getLogger(__name__)
REFUSED = 2
MAX_USD_LIMIT = Decimal(100)
MICROS = Decimal(1_000_000)
MAX_REPEAT = 10
MODEL_NAME = re.compile(r"[A-Za-z0-9._:-]{1,100}")  # as Settings.agent_model
EFFORTS = ("low", "medium", "high", "xhigh", "max")


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


def install_model_allowlist() -> object:
    """The worker's list with this run's model host, built as the worker's entry point builds it
    for a usable provider (ADR 0012 §2). The smoke set runs Anthropic only (precondition 3)."""
    return install_worker_allowlist(("anthropic",))


def check_preconditions(settings: Settings, args_live: bool) -> None:
    if not args_live:
        raise LiveRefused(
            "pass --live to run the smoke set against the real model; "
            "the offline set runs in the backend tests (tests/test_evals_smoke.py)"
        )
    if "CI" in os.environ:
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


def plan_text(
    config: AgentConfig, cases: Sequence[Case], budget_micros: int | None, repeat: int = 1
) -> str:
    limits = config.limits
    runs = len(cases) * repeat
    ceiling = run_ceiling_micros(config, runs, budget_micros)
    repeats = f" x {repeat} repeats = {runs} runs" if repeat > 1 else ""
    over = case_ceiling_micros(config) - limits.job_cost_micros
    return "\n".join(
        [
            f"classbeat smoke set, LIVE: {len(cases)} cases{repeats}, model {config.model}, "
            f"effort {config.effort}",
            f"limits per case (ADR 0006 section 5): {limits.max_turns} turns, "
            f"{limits.max_tool_calls} tool calls, {limits.max_corrections} corrections, "
            f"{limits.stage_seconds:.0f} s, {usd(limits.job_cost_micros)} job ceiling",
            f"estimated cost ceiling for this run: {usd(ceiling)} "
            f"(worst case; the budget is {usd(budget_micros) if budget_micros else 'not set'}; "
            f"the total can exceed it by one turn's over-run, up to {usd(over)})",
            "synthetic music only; no user account is touched",
        ]
    )


def with_model(
    config: AgentConfig, *, model: str | None = None, effort: str | None = None
) -> AgentConfig:
    """The config with another model or effort; everything else as it was."""
    return AgentConfig(
        model or config.model,
        effort or config.effort,
        config.max_tokens,
        config.limits,
        config.prices,
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
    repeat: int = 1,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    left = budget_micros
    for index in range(repeat):
        for case in cases:
            if left <= 0:
                print(f"budget used up; {case.name} and the rest not run", file=out)
                return results
            limited = with_job_ceiling(config, min(config.limits.job_cost_micros, left))
            try:
                result = await run_case(case, provider_factory(), limited, repeat=index)
            except Exception as exc:  # the class name only: a message could hold request data
                logger.error(
                    "smoke case failed", extra={"case": case.name, "error": type(exc).__name__}
                )
                result = CaseResult(
                    case.name,
                    f"error:{type(exc).__name__}",
                    model=config.model,
                    effort=config.effort,
                    repeat=index,
                )
            left -= result.cost_micros
            results.append(result)
            label = f"{case.name} #{index + 1}" if repeat > 1 else case.name
            print(f"{label}: {'pass' if result.passed else 'FAIL'} ({result.outcome})", file=out)
            for line in failed_check_lines(result):
                print(line, file=out)
    return results


def main_live(
    settings: Settings,
    *,
    live: bool,
    max_usd: str | None,
    case_names: Sequence[str] | None,
    out_path: Path | None,
    model: str | None = None,
    effort: str | None = None,
    repeat: int = 1,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    provider_factory: Callable[[Settings], ModelProvider] = provider_for,
    install: Callable[[], object] = install_model_allowlist,
) -> int:
    try:
        check_preconditions(settings, live)
        config = AgentConfig.from_settings(settings)
        if model is not None:
            if not MODEL_NAME.fullmatch(model):
                raise LiveRefused("--model is not a model id")
            config = with_model(config, model=model)
        if effort is not None:
            if effort not in EFFORTS:
                raise LiveRefused(f"--effort must be one of {', '.join(EFFORTS)}")
            config = with_model(config, effort=effort)
        if not 1 <= repeat <= MAX_REPEAT:
            raise LiveRefused(f"--repeat must be from 1 to {MAX_REPEAT}")
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
        print(plan_text(config, cases, budget, repeat), file=out)
        if budget is None:
            raise LiveRefused("not run: pass --max-usd N to accept spending up to N dollars")
        # Before the provider is built: every request goes through the allowlist.
        install()
        provider_factory(settings)  # builds once to fail early on a missing key; discarded
    except LiveRefused as refused:
        print(str(refused), file=err)
        return REFUSED
    except ProviderUnavailable as unavailable:
        print(f"refusing: {unavailable}", file=err)
        return REFUSED
    results = asyncio.run(
        run_live(cases, lambda: provider_factory(settings), config, budget, out, repeat)
    )
    print(results_table(results), file=out)
    if out_path is not None:
        out_path.write_text(results_json(results), encoding="utf-8")
    return 0 if results and all(r.passed for r in results) else 1
