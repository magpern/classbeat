"""Run one ``interpret`` case (ADR 0029 section 4): the stage on a provider, then its checks.

The stage is given what the case holds, as the app gives it (``InterpretContext``). The result is
a ``CaseResult`` like a design run's, so the suite's rows, gate and cost rules need nothing new:
the checks of ``interpret_checks`` plus ``limits`` (turns, tool calls and cost within the run's
limits). No draft, so no duration or TSS.
"""

import time
from collections.abc import Callable

from classbeat.agent.interpret import InterpretContext, InterpretResult, interpret_stage
from classbeat.agent.loop import AgentConfig, run_stage
from classbeat.agent.provider import ModelProvider
from classbeat.evals.checks import Check
from classbeat.evals.interpret_cases import InterpretCase
from classbeat.evals.interpret_checks import check_interpretation
from classbeat.evals.runner import CaseResult, EvalHost
from classbeat.jobs.errors import JobFailure


def context_for(case: InterpretCase) -> InterpretContext:
    return InterpretContext(
        case.request,
        sports=tuple(case.sports),
        max_class_minutes=case.max_class_minutes,
        games_allowed=case.games_allowed,
        music=tuple(m.named() for m in case.music),
    )


async def run_interpret_case(
    case: InterpretCase,
    provider: ModelProvider,
    config: AgentConfig,
    *,
    repeat: int = 0,
    clock: Callable[[], float] = time.monotonic,
) -> CaseResult:
    ctx = context_for(case)
    host = EvalHost()
    started = clock()
    try:
        done = await run_stage(host, provider, interpret_stage(ctx), ctx.first_message(), config)
    except JobFailure as failure:
        return _failed(case, host, config, repeat, failure.category, clock() - started)
    except Exception as exc:  # the class name only: a message could hold request data
        return _failed(case, host, config, repeat, f"error:{type(exc).__name__}", clock() - started)
    if not isinstance(done.output, InterpretResult):
        return _failed(case, host, config, repeat, "error:BadOutput", clock() - started)
    limits = config.limits
    checks = [
        *check_interpretation(case, done.output.request),
        Check(
            "limits",
            done.turns <= limits.max_turns
            and done.tool_calls <= limits.max_tool_calls
            and done.cost_micros <= limits.job_cost_micros,
            f"{done.turns} turns of {limits.max_turns}, {done.tool_calls} tool calls of "
            f"{limits.max_tool_calls}, cost {done.cost_micros} of {limits.job_cost_micros} "
            "micro-dollars",
        ),
    ]
    return CaseResult(
        case.name,
        "submitted",
        checks,
        done.turns,
        done.tool_calls,
        done.cost_micros,
        done.info.prompt_version,
        done.model_answered,
        model=config.model,
        effort=config.effort,
        repeat=repeat,
        corrections=done.corrections,
        wall_sec=clock() - started,
        usage=done.usage,
    )


def _failed(
    case: InterpretCase, host: EvalHost, config: AgentConfig, repeat: int, outcome: str, wall: float
) -> CaseResult:
    c = host.counters  # what the run spent still counts, whatever stopped it
    return CaseResult(
        case.name,
        outcome,
        turns=c.turns,
        tool_calls=c.tool_calls,
        cost_micros=c.cost_micros,
        prompt_version=host.info.prompt_version if host.info else "",
        model_answered=c.model_answered,
        model=config.model,
        effort=config.effort,
        repeat=repeat,
        corrections=c.corrections,
        wall_sec=wall,
        usage=c.usage,
    )
