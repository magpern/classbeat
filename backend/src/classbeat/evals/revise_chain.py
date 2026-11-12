"""The interpret step of a chained revise case (ADR 0029 section 4, #908).

A revise job of a class that states values reads the request with the ``interpret`` stage first
and layers what it read onto the brief (``design.revise_stated``). A case with
``ReviseSpec.interpret`` does the same, on the case's brief as the base, so the reading and the
layering are gated together with the revise it leads to (the interpret cases and the stated
revisions are gated apart). ``read_revision`` returns the layered brief and what the interpret
run spent; ``counted`` adds that to the revise run's result, so a case's cost is both runs.
"""

from dataclasses import dataclass, replace
from typing import Protocol

from classbeat.agent.brief import Brief
from classbeat.agent.interpret import InterpretResult, interpret_stage
from classbeat.agent.loop import AgentConfig, Counters, StageHost, run_stage
from classbeat.agent.provider import ModelProvider
from classbeat.design.revise_layer_policy import layer
from classbeat.design.revise_stated import context_for
from classbeat.evals.cases import Case
from classbeat.evals.results import CaseResult
from classbeat.jobs.errors import JobFailure


class HostWithCounters(StageHost, Protocol):
    """The runner's ``EvalHost``: a ``StageHost`` that keeps what the run spent."""

    counters: Counters


@dataclass(frozen=True)
class Reading:
    brief: Brief  # the layered brief the revise stage gets
    spent: Counters  # the interpret run's turns, tool calls, cost and usage


class ReadFailed(Exception):
    """The interpret run failed: ``outcome`` is the case's outcome, ``spent`` what it cost."""

    def __init__(self, outcome: str, spent: Counters) -> None:
        super().__init__(outcome)
        self.outcome = outcome
        self.spent = spent


async def read_revision(
    case: Case, provider: ModelProvider, config: AgentConfig, host: HostWithCounters
) -> Reading:
    if case.revise is None:
        raise ValueError("a chained case is a revise case")
    ctx = context_for(case.brief, case.revise.request, None)
    try:
        done = await run_stage(host, provider, interpret_stage(ctx), ctx.first_message(), config)
    except JobFailure as failure:
        raise ReadFailed(failure.category, host.counters) from None
    except Exception as exc:  # the class name only: a message could hold request data
        raise ReadFailed(f"error:{type(exc).__name__}", host.counters) from None
    if not isinstance(done.output, InterpretResult):
        raise ReadFailed("error:BadOutput", host.counters)
    return Reading(layer(case.brief, done.output.request, None).brief, host.counters)


def counted(result: CaseResult, spent: Counters) -> CaseResult:
    """``result`` with the interpret run's spend added to the revise run's."""
    return replace(
        result,
        turns=result.turns + spent.turns,
        tool_calls=result.tool_calls + spent.tool_calls,
        cost_micros=result.cost_micros + spent.cost_micros,
        corrections=result.corrections + spent.corrections,
        usage=result.usage + spent.usage,
    )
