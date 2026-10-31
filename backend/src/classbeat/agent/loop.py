"""One run of a stage (ADR 0006 sections 1 and 5).

``run_stage(host, provider, stage, first_message, config)`` opens ``conv = provider.start(spec)``,
then repeats:

1. checkpoint (``host.checkpoint()``: cancel, lease, shutdown; ADR 0005 section 4) and the limits
   (turns, wall time, the cost check of ``classbeat.agent.limits``);
2. ``turn = await conv.turn(results)``; record the turn's usage and cost (``host.record``, fenced,
   after every turn, so a crashed run still shows its cost);
3. ``refusal`` ends the run ``model_refused``; ``max_tokens`` and ``other`` end it
   ``invalid_output``;
4. tool calls run in order, each after a checkpoint and the tool-call limit, through the
   dispatcher; all their results go back together in the next turn. A successful final tool ends
   the run ``submitted`` with its output; a refused one is a correction round, and one more than
   ``max_corrections`` ends the run ``invalid_output``;
5. a turn without tool calls gets one tool-less reminder to call the final tool (it counts as a
   turn); a second such turn ends the run ``invalid_output``.

A provider failure ends the run with its category (``model_unavailable`` is retried by the job,
ADR 0005 section 4). The run's ``agent_runs`` row gets its outcome, then ``JobFailure(category)``
is raised; a cancel (``RunStopped("cancelled")``) records ``cancelled`` and re-raises. Anything
else raised inside the run (a lent read's job failure, such as a lost Spotify connection during
``search_tracks``) records the category the job fails with (``classbeat.jobs.failure_of``,
``internal`` for an unknown error) and propagates. A lost lease records nothing more (the fence
refuses it) and neither does a shutdown hand-back: that row keeps outcome NULL with the counters
of what it spent, and the next attempt opens a new row.

Model text, tool arguments and results stay in memory for the run and are never logged or stored;
events carry the tool's stage name (or ``unknown``) and an error code only.
"""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from uuid import UUID

from classbeat.agent.dispatcher import dispatch_async
from classbeat.agent.limits import Limits, tokens_for
from classbeat.agent.prices import DEFAULT_PRICES, Price, PriceTable
from classbeat.agent.provider import ModelProvider, ProviderError, StageSpec, ToolResult, Usage
from classbeat.agent.stage import Stage
from classbeat.config import Settings
from classbeat.jobs.errors import JobFailure, LeaseLost, RunStopped, failure_of

REMINDER: Final = "Finish by calling {tool} with the final draft."


@dataclass(frozen=True, slots=True)
class AgentConfig:
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_tokens: int = 32_000
    limits: Limits = field(default_factory=Limits)
    prices: PriceTable = DEFAULT_PRICES

    @classmethod
    def from_settings(
        cls, settings: Settings, prices: PriceTable = DEFAULT_PRICES
    ) -> "AgentConfig":
        return cls(
            model=settings.agent_model,
            effort=settings.agent_effort,
            max_tokens=settings.agent_max_tokens,
            limits=Limits.from_settings(settings),
            prices=prices,
        )


@dataclass(frozen=True, slots=True)
class RunInfo:
    stage: str
    provider: str
    model: str
    effort: str
    prompt_version: str


@dataclass
class Counters:
    turns: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_micros: int = 0
    model_answered: str = ""
    corrections: int = 0  # final-tool calls the stage refused (the model corrected its draft)


class StageHost(Protocol):
    """Where a stage run checkpoints and records itself; ``classbeat.agent.records.JobStageHost``
    in the worker (every write fenced)."""

    async def checkpoint(self) -> None: ...

    async def begin(self, info: RunInfo) -> tuple[UUID, int]:
        """Open the run's ``agent_runs`` row; returns its id and what the job spent before."""
        ...

    async def record(self, run_id: UUID, counters: Counters) -> None: ...

    async def tool_called(self, tool: str, error: str | None) -> None: ...

    async def finish(self, run_id: UUID, counters: Counters, outcome: str) -> None: ...


@dataclass(frozen=True)
class StageOutcome:
    output: Any
    run_id: UUID
    info: RunInfo
    turns: int
    tool_calls: int
    usage: Usage
    cost_micros: int
    model_answered: str
    corrections: int = 0


class _Stop(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _spec_chars(spec: StageSpec) -> int:
    tools = json.dumps([[t.name, t.description, t.input_schema] for t in spec.tools])
    return len(spec.system) + len(tools) + len(spec.first_message)


async def run_stage(
    host: StageHost,
    provider: ModelProvider,
    stage: Stage,
    first_message: str,
    config: AgentConfig,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> StageOutcome:
    spec = StageSpec(
        stage=stage.name,
        system=stage.system,
        tools=stage.definitions(),
        first_message=first_message,
        model=config.model,
        effort=config.effort,
        max_tokens=config.max_tokens,
        final_tool=stage.final_tool,
    )
    info = RunInfo(stage.name, provider.name, config.model, config.effort, stage.prompt_version)
    run_id, spent_before = await host.begin(info)
    counters = Counters()
    try:
        price = config.prices.lookup(provider.name, config.model)
        if price is None:
            raise _Stop("model_config")
        output = await _loop(
            host, provider, stage, spec, config.limits, price, spent_before, run_id, counters, clock
        )
    except _Stop as stop:
        await host.finish(run_id, counters, stop.category)
        raise JobFailure(stop.category) from None
    except RunStopped as stopped:
        if stopped.reason == "cancelled":
            await host.finish(run_id, counters, "cancelled")
        raise
    except (LeaseLost, asyncio.CancelledError):
        raise
    except Exception as exc:  # a lent read's job failure keeps its category (#198 item 15)
        await host.finish(run_id, counters, failure_of(exc).category)
        raise
    await host.finish(run_id, counters, "submitted")
    return StageOutcome(
        output,
        run_id,
        info,
        counters.turns,
        counters.tool_calls,
        counters.usage,
        counters.cost_micros,
        counters.model_answered,
        counters.corrections,
    )


async def _loop(
    host: StageHost,
    provider: ModelProvider,
    stage: Stage,
    spec: StageSpec,
    limits: Limits,
    price: Price,
    spent_before: int,
    run_id: UUID,
    counters: Counters,
    clock: Callable[[], float],
) -> Any:
    started = clock()
    conv = provider.start(spec)
    results: list[ToolResult] = []
    reminder: str | None = None
    pending_chars = _spec_chars(spec)
    last_prompt = last_output = 0
    reminded = False

    def out_of_time() -> bool:
        return clock() - started > limits.stage_seconds

    while True:
        await host.checkpoint()
        if counters.turns >= limits.max_turns or out_of_time():
            raise _Stop("limit_exceeded")
        estimate = last_prompt + last_output + tokens_for(pending_chars)
        projected = (
            spent_before
            + counters.cost_micros
            + price.prompt_micros(estimate)
            + price.output_micros(limits.turn_output_reserve)
        )
        if projected > limits.job_cost_micros:
            raise _Stop("limit_exceeded")
        try:
            turn = await conv.turn(tuple(results), reminder=reminder)
        except ProviderError as exc:
            raise _Stop(exc.category) from None
        counters.turns += 1
        counters.usage = counters.usage + turn.usage
        counters.cost_micros += price.cost_micros(turn.usage)
        counters.model_answered = turn.model[:100]
        await host.record(run_id, counters)
        last_prompt, last_output = turn.usage.prompt_tokens, turn.usage.output_tokens
        results, reminder, pending_chars = [], None, 0

        if turn.stop == "refusal":
            raise _Stop("model_refused")
        if turn.stop in ("max_tokens", "other"):
            raise _Stop("invalid_output")
        if not turn.tool_calls:
            if reminded:
                raise _Stop("invalid_output")
            reminded = True
            reminder = REMINDER.format(tool=stage.final_tool)
            pending_chars = len(reminder)
            continue
        for call in turn.tool_calls:
            await host.checkpoint()
            if counters.tool_calls >= limits.max_tool_calls or out_of_time():
                raise _Stop("limit_exceeded")
            counters.tool_calls += 1
            done = await dispatch_async(stage, call)
            await host.tool_called(done.tool, done.error)
            if done.final:
                if done.ok:
                    return done.output
                counters.corrections += 1
                if counters.corrections > limits.max_corrections:
                    raise _Stop("invalid_output")
            results.append(done.result)
            pending_chars += len(done.result.content)
