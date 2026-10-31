"""``python -m classbeat.evals suite --live``: the owner's live run of the regression suite against
any catalog model, optionally recorded for the enable gate (ADR 0012 §4.4, §5 "Who writes a
result"). Not run by CI or by an agent; it spends the owner's money, and each run needs the
owner's go-ahead.

**Two layers.** The CLI entry (``classbeat.evals.__main__``) checks every precondition
(``check_preconditions``) and then calls ``main_suite`` with the real
``eval_provider_for_choice`` (``provider_for_choice`` with DeepSeek's eval-only path), the real
allowlist installer (``install_choice_allowlist``) and the worker's database session
(``worker_session``). ``main_suite`` takes those three, and the suite, as **keyword-only**
parameters that no command-line argument sets; tests inject a replay provider there.

**Preconditions** (each refusal is exit 2 and one line naming the rule, never a value):

1. ``--live``; 2. no ``CI`` (set at all); 3. the provider and model in ``classbeat.agent.catalog``
   with that effort (so ``fake``, which the catalog never lists, is refused); 4. the provider's key
   file holds a key; 5. none of the refused model SDK variables is set (ADR 0012 §2);
6. after the plan (the runs, the limits and the **cost ceiling**) is printed, ``--max-usd N``.

``main_suite`` then installs the worker's allowlist with **only the chosen provider's** model host
(``install_worker_allowlist((provider,))``), before the provider is built; refuses (exit 2,
nothing written) unless every provider it builds, the first and each run's, has the choice's
provider as its adapter class (``provider_kind``: the class, never ``provider.name``); and runs
both stage groups: every design and revise case (``runner.run_case``) and every song case on the
synthetic catalogue (``pick.run_pick_case``), each ``--repeat`` times, round after round, with
what is left of the budget as each run's job ceiling. A failed run still costs what it spent (the
host's counters), and a run whose spend is unknown (an exception the runner did not catch) is
charged everything that was left: cost errs high.

**The file** (``--out``) is format 2 (``runner.suite_results``): the header carries the
catalog-checked choice (never ``provider.name`` or ``Turn.model``), the running prompt versions and
the built provider's ``name``. **The verdict** is ``gate.evaluate`` of that file's own bytes
against the manifest of the digest-verified suite this run executed (``suite.load_suite``), never
a manifest from the file or the arguments.

**``--record --authorization TEXT``** writes one ``model_eval_results`` row per stage group the
file has, in one transaction, through ``classbeat.models.store.record_result`` only (the static
guard of #211): the choice, the manifest's ``suite_version`` and digest, the group's running
prompt versions, the recomputed verdict, the file's SHA-256, the group's total cost and the
authorization text. It refuses again, before writing, when the built provider's kind is not the
choice's provider (so a ``fake`` never records).

Synthetic music and catalogue only: no user account is touched, and only the model is real.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TextIO

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent import catalog, choose_songs, pick_pool
from classbeat.agent.limits import Limits
from classbeat.agent.loop import AgentConfig
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers import (
    AnthropicProvider,
    DeepSeekProvider,
    OpenAIProvider,
    ProviderUnavailable,
)
from classbeat.config import Settings, model_sdk_environment_problems
from classbeat.egress import install_worker_allowlist
from classbeat.evals.gate import evaluate
from classbeat.evals.live import (
    MAX_REPEAT,
    MAX_USD_LIMIT,
    MICROS,
    usd,
    with_job_ceiling,
    with_model,
)
from classbeat.evals.pick import PickCaseResult, run_pick_case
from classbeat.evals.runner import (
    SuiteHeader,
    failed_check_lines,
    run_case,
    song_suite_row,
    suite_results_json,
    suite_row,
)
from classbeat.evals.suite import Group, Suite, load_suite
from classbeat.models.routes import ModelChoice
from classbeat.models.store import file_sha256, record_result
from classbeat.models.versions import all_prompt_versions, running_prompt_versions

if TYPE_CHECKING:
    from classbeat.evals.gate import Verdict

logger = logging.getLogger(__name__)
REFUSED: Final = 2
AUTHORIZATION_MAX: Final = 200
# The adapter class of each provider (ADR 0012 §4.4): what was built, not what it calls itself.
ADAPTERS: Final[dict[type, str]] = {
    AnthropicProvider: "anthropic",
    OpenAIProvider: "openai",
    DeepSeekProvider: "deepseek",
}

ProviderFactory = Callable[[Settings, ModelChoice], ModelProvider]
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


WRONG_ADAPTER = "refusing: the built provider is not the chosen provider's adapter"


class SuiteRefused(Exception):
    """The live suite run may not start (or record); the message names the rule only."""


@dataclass(frozen=True, slots=True)
class Run:
    group: Group
    case: str
    repeat: int


def provider_kind(provider: ModelProvider) -> str | None:
    """The provider of the built adapter, by its exact class (``None`` for any other, ``fake``
    included, whatever its ``name``)."""
    return ADAPTERS.get(type(provider))


def install_choice_allowlist(choice: ModelChoice) -> object:
    """The worker's allowlist with the chosen provider's model host and no other (ADR 0012 §2)."""
    return install_worker_allowlist((choice.provider,))


@asynccontextmanager
async def _worker_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    from classbeat.db import (
        create_engine,
        create_session_factory,
        system_session,
        verify_database_role,
    )

    engine = create_engine(settings)
    try:
        await verify_database_role(engine)
        async with system_session(create_session_factory(engine)) as db:
            yield db
    finally:
        await engine.dispose()


def worker_session(settings: Settings) -> SessionOpener:
    """The worker's database transaction (the app role), opened only when recording."""
    return lambda: _worker_session(settings)


def check_preconditions(
    settings: Settings, *, live: bool, provider: str, model: str, effort: str
) -> ModelChoice:
    """The command's preconditions 1 to 5 (see the module); the catalog-checked choice."""
    if not live:
        raise SuiteRefused(
            "pass --live to run the suite against the real model; "
            "the offline suite runs in the backend tests"
        )
    if "CI" in os.environ:
        raise SuiteRefused("refusing to run live when CI is set")
    entry = catalog.find(provider, model)
    if entry is None:
        raise SuiteRefused("refusing: the provider and model are not in the catalog")
    if effort not in entry.efforts:
        raise SuiteRefused("refusing: the catalog entry does not list that effort")
    if settings.model_key(entry.provider) is None:
        raise SuiteRefused(f"refusing: provider {entry.provider} needs its key file")
    refused = model_sdk_environment_problems()
    if refused:
        raise SuiteRefused("refusing: the worker must not set " + ", ".join(refused))
    return ModelChoice(entry.provider, entry.model, effort)


def run_order(suite: Suite, repeat: int) -> list[Run]:
    """Every run, one round of all cases after the other (a budget that runs out leaves every
    case of the earlier rounds run)."""
    order: list[Run] = []
    for index in range(repeat):
        order += [Run("design", name, index) for name in sorted(suite.design_cases)]
        order += [Run("songs", name, index) for name in sorted(suite.song_cases)]
    return order


@dataclass(frozen=True, slots=True)
class Budgets:
    """Each group's configuration and per-run worst case (the job ceiling plus one turn's
    over-run per stage, ADR 0006 section 5)."""

    design: AgentConfig
    pool: Limits
    choose: Limits
    over_micros: int  # one turn's over-run

    @classmethod
    def build(cls, settings: Settings, choice: ModelChoice) -> "Budgets":
        config = with_model(
            AgentConfig.from_settings(settings), model=choice.model, effort=choice.effort
        )
        price = config.prices.lookup(choice.provider, choice.model)
        if price is None:
            raise SuiteRefused(f"the model {choice.model} has no price in classbeat.agent.prices")
        base = config.limits
        pool = base.for_song_stage(
            settings, max_turns=pick_pool.MAX_TURNS, max_tool_calls=pick_pool.MAX_TOOL_CALLS
        )
        choose = base.for_song_stage(
            settings, max_turns=choose_songs.MAX_TURNS, max_tool_calls=choose_songs.MAX_TOOL_CALLS
        )
        over = price.output_micros(max(config.max_tokens - base.turn_output_reserve, 0))
        return cls(config, pool, choose, over)

    def ceiling(self, group: Group) -> int:
        if group == "design":
            return self.design.limits.job_cost_micros + self.over_micros
        return self.pool.job_cost_micros + 2 * self.over_micros  # two stages

    def run_ceiling(self, runs: list[Run], budget_micros: int | None) -> int:
        full = sum(self.ceiling(r.group) for r in runs)
        if budget_micros is None:
            return full
        return min(full, budget_micros + 2 * self.over_micros)


def plan_text(
    choice: ModelChoice, suite: Suite, budgets: Budgets, runs: list[Run], budget: int | None
) -> str:
    design = budgets.design.limits
    return "\n".join(
        [
            f"classbeat regression suite {suite.manifest.suite_version}, LIVE: provider "
            f"{choice.provider}, model {choice.model}, effort {choice.effort}",
            f"{len(suite.design_cases)} design cases and {len(suite.song_cases)} song cases, "
            f"{len(runs)} runs",
            f"limits per design run: {design.max_turns} turns, {design.max_tool_calls} tool "
            f"calls, {design.max_corrections} corrections, {usd(design.job_cost_micros)} ceiling; "
            f"per song run {usd(budgets.pool.job_cost_micros)}",
            f"estimated cost ceiling for this run: {usd(budgets.run_ceiling(runs, budget))} "
            f"(worst case; the budget is {usd(budget) if budget else 'not set'})",
            "synthetic music and catalogue only; no user account is touched",
        ]
    )


def _budget(max_usd: str | None) -> int | None:
    if max_usd is None:
        return None
    try:
        amount = Decimal(max_usd)
    except ArithmeticError:
        amount = Decimal(0)
    if not (amount.is_finite() and 0 < amount <= MAX_USD_LIMIT):
        raise SuiteRefused(f"--max-usd must be above 0 and at most {MAX_USD_LIMIT}")
    return int(amount * MICROS)


def _authorization(record: bool, authorization: str | None) -> str | None:
    if not record:
        if authorization is not None:
            raise SuiteRefused("--authorization is only for --record")
        return None
    text = (authorization or "").strip()
    if not 1 <= len(text) <= AUTHORIZATION_MAX or not text.isprintable():
        raise SuiteRefused(
            f"--record needs --authorization: 1 to {AUTHORIZATION_MAX} printable characters"
        )
    return text


def _limited(limits: Limits, micros: int) -> Limits:
    return Limits(
        limits.max_turns,
        limits.max_tool_calls,
        limits.max_corrections,
        limits.stage_seconds,
        min(limits.job_cost_micros, micros),
        limits.turn_output_reserve,
    )


async def _one(
    run: Run,
    suite: Suite,
    provider: ModelProvider,
    budgets: Budgets,
    left: int,
    choice: ModelChoice,
) -> tuple[dict[str, Any], int, bool, list[str]]:
    """One run: its format-2 row, what it spent, whether it passed and its failed-check lines."""
    category = suite.category_of(run.case)
    margin = suite.manifest.zone7_tss_margin
    if run.group == "design":
        config = with_job_ceiling(budgets.design, min(budgets.design.limits.job_cost_micros, left))
        case = suite.design_cases[run.case]
        result = await run_case(case, provider, config, repeat=run.repeat, zone7_tss_margin=margin)
        lines = failed_check_lines(result)
        return suite_row(result, category), result.cost_micros, result.passed, lines
    limits = (_limited(budgets.pool, left), _limited(budgets.choose, left))
    song = suite.song_cases[run.case]
    try:
        picked, _, _ = await run_pick_case(
            song, provider, budgets.design, limits, suite.manifest.bpm_bands
        )
    except Exception as exc:  # the class name only: a message could hold request data
        # What it spent is unknown: charge the run's whole ceiling, so cost errs high.
        picked = PickCaseResult(run.case, f"error:{type(exc).__name__}", cost_micros=left)
    row = song_suite_row(
        picked, category, repeat=run.repeat, model=choice.model, effort=choice.effort
    )
    lines = [f"  {run.case} failed {c.name}: {c.detail}" for c in picked.checks if not c.ok]
    return row, picked.cost_micros, picked.passed, lines


async def run_suite(
    suite: Suite,
    runs: list[Run],
    provider_factory: Callable[[], ModelProvider],
    budgets: Budgets,
    budget_micros: int,
    choice: ModelChoice,
    out: TextIO,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    left = budget_micros
    for run in runs:
        if left <= 0:
            print(f"budget used up; {run.case} #{run.repeat + 1} and the rest not run", file=out)
            break
        row, spent, passed, lines = await _one(
            run, suite, provider_factory(), budgets, left, choice
        )
        left -= spent
        rows.append(row)
        verdict = "pass" if passed else "FAIL"
        print(f"{run.case} #{run.repeat + 1}: {verdict} ({row['outcome']})", file=out)
        for line in lines:
            print(line, file=out)
    return rows


def verdict_lines(verdict: "Verdict") -> Iterator[str]:
    for group, v in verdict.groups.items():
        rules = f" (rules failed: {', '.join(v.rules_failed)})" if v.rules_failed else ""
        failing = {k: c.rules_failed for k, c in v.categories.items() if c.rules_failed}
        cats = "".join(f"; {k}: {', '.join(r)}" for k, r in failing.items())
        yield f"verdict {group}: {v.status}, {v.passes} of {v.runs} runs passed{rules}{cats}"


async def record(
    open_session: SessionOpener,
    verdict: "Verdict",
    choice: ModelChoice,
    document: bytes,
    rows: list[dict[str, Any]],
    suite: Suite,
    authorization: str,
) -> list[uuid.UUID]:
    """One row per stage group of the verdict, in one transaction (``record_result`` only)."""
    sha = file_sha256(document)
    run_id = uuid.uuid4()
    index = suite.manifest.case_index()
    header = json.loads(document)
    recorded: list[uuid.UUID] = []
    async with open_session() as db:
        for group, gv in verdict.groups.items():
            cost = sum(int(r["cost_micros"]) for r in rows if index[r["case"]][1] == group)
            row = await record_result(
                db,
                run_id=run_id,
                suite_version=suite.manifest.suite_version,
                manifest_digest=suite.manifest.digest,
                provider=choice.provider,
                model=choice.model,
                effort=choice.effort,
                stage_group=group,
                prompt_versions=running_prompt_versions(group),
                classbeat_version=str(header["classbeat_version"]),
                results_sha256=sha,
                verdict=gv.as_json(),
                total_cost_micros=cost,
                authorization_note=authorization,
            )
            recorded.append(row.id)
    return recorded


def main_suite(
    settings: Settings,
    *,
    choice: ModelChoice,
    repeat: int,
    max_usd: str | None,
    out_path: Path,
    record_run: bool = False,
    authorization: str | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    provider_factory: ProviderFactory,
    install: Callable[[ModelChoice], object],
    open_session: SessionOpener,
    suite: Suite | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Run the suite for ``choice`` (already catalog-checked by ``check_preconditions``); see
    the module. ``provider_factory``, ``install``, ``open_session`` and ``suite`` are the seams
    the CLI entry fills with the real ones (``suite`` None: ``load_suite()``)."""
    try:
        note = _authorization(record_run, authorization)
        if not 1 <= repeat <= MAX_REPEAT:
            raise SuiteRefused(f"--repeat must be from 1 to {MAX_REPEAT}")
        loaded = suite if suite is not None else load_suite()
        budgets = Budgets.build(settings, choice)
        runs = run_order(loaded, repeat)
        budget = _budget(max_usd)
        print(plan_text(choice, loaded, budgets, runs, budget), file=out)
        if budget is None:
            raise SuiteRefused("not run: pass --max-usd N to accept spending up to N dollars")
        install(choice)  # before the provider is built: every request goes through the list

        def checked() -> ModelProvider:
            # Every build, not only the first: a run never uses another adapter.
            provider = provider_factory(settings, choice)
            if provider_kind(provider) != choice.provider:
                raise SuiteRefused(WRONG_ADAPTER)
            return provider

        built = checked()
    except SuiteRefused as refused:
        print(str(refused), file=err)
        return REFUSED
    except ProviderUnavailable as unavailable:
        print(f"refusing: {unavailable}", file=err)
        return REFUSED
    started = clock()
    try:
        rows = asyncio.run(run_suite(loaded, runs, checked, budgets, budget, choice, out))
    except SuiteRefused as refused:  # a later build was another adapter: nothing is written
        print(str(refused), file=err)
        return REFUSED
    header = SuiteHeader(
        suite_version=loaded.manifest.suite_version,
        manifest_digest=loaded.manifest.digest,
        provider=choice.provider,
        model=choice.model,
        effort=choice.effort,
        prompt_versions=all_prompt_versions(),
        repeat=repeat,
        started_at=started,
        ended_at=clock(),
        provider_kind=built.name,
    )
    document = suite_results_json(header, rows).encode("utf-8")
    out_path.write_bytes(document)
    verdict = evaluate(json.loads(document), loaded.manifest)
    for line in verdict_lines(verdict):
        print(line, file=out)
    passed = bool(verdict.groups) and all(v.passed for v in verdict.groups.values())
    if note is None:
        return 0 if passed else 1
    if provider_kind(built) != choice.provider:  # again, right before writing
        print("not recorded: the built provider is not the chosen provider's adapter", file=err)
        return REFUSED
    ids = asyncio.run(record(open_session, verdict, choice, document, rows, loaded, note))
    for result_id in ids:
        print(f"recorded result {result_id}", file=out)
    return 0 if passed else 1
