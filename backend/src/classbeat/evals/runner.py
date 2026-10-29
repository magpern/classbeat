"""One smoke case through the real design stage (ADR 0006 section 8).

``run_case(case, provider, config)`` builds the case's synthetic music, runs
``classbeat.agent.run_stage`` with the given provider on an in-memory host (no database, no job),
and runs the checks on the draft it submitted. A run that fails (a limit, a refusal, a provider
error) is a failed case whose outcome is the failure category.

``CaseResult`` holds numbers, check names and the category only: no model text, no draft text, no
ids. ``results_table`` and ``results_json`` are what the owner pastes into a PR.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from classbeat.agent import DesignContext, DesignResult, design_stage, run_stage
from classbeat.agent.loop import AgentConfig, Counters, RunInfo
from classbeat.agent.provider import ModelProvider
from classbeat.evals.cases import Case
from classbeat.evals.checks import Check, run_checks
from classbeat.evals.music import build_music
from classbeat.jobs.errors import JobFailure


class EvalHost:
    """A ``StageHost`` without a database: nothing to fence, nothing stored."""

    def __init__(self) -> None:
        self.counters = Counters()
        self.info: RunInfo | None = None

    async def checkpoint(self) -> None:
        return None

    async def begin(self, info: RunInfo) -> tuple[uuid.UUID, int]:
        self.info = info
        return uuid.uuid4(), 0

    async def record(self, run_id: uuid.UUID, counters: Counters) -> None:
        self.counters = counters

    async def tool_called(self, tool: str, error: str | None) -> None:
        return None

    async def finish(self, run_id: uuid.UUID, counters: Counters, outcome: str) -> None:
        self.counters = counters


@dataclass(frozen=True)
class CaseResult:
    name: str
    outcome: str  # "submitted" or the failure category
    checks: list[Check] = field(default_factory=lambda: list[Check]())
    turns: int = 0
    tool_calls: int = 0
    cost_micros: int = 0
    prompt_version: str = ""
    model_answered: str = ""
    duration_sec: int = 0
    tss: float = 0.0

    @property
    def passed(self) -> bool:
        return self.outcome == "submitted" and bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


async def run_case(case: Case, provider: ModelProvider, config: AgentConfig) -> CaseResult:
    ctx = DesignContext.build(case.brief, build_music(case.music))
    host = EvalHost()

    def failed(outcome: str) -> CaseResult:
        # What the run spent still counts against the budget, whatever stopped it.
        c = host.counters
        return CaseResult(
            case.name,
            outcome,
            turns=c.turns,
            tool_calls=c.tool_calls,
            cost_micros=c.cost_micros,
            prompt_version=host.info.prompt_version if host.info else "",
            model_answered=c.model_answered,
        )

    try:
        done = await run_stage(host, provider, design_stage(ctx), ctx.first_message(), config)
    except JobFailure as failure:
        return failed(failure.category)
    except Exception as exc:  # the class name only: a message could hold request data
        return failed(f"error:{type(exc).__name__}")
    result: DesignResult = done.output
    try:
        checks = run_checks(case, result, done, config.limits, float(ctx.map["duration"]))
    except Exception as exc:
        return failed(f"error:{type(exc).__name__}")
    return CaseResult(
        case.name,
        "submitted",
        checks,
        done.turns,
        done.tool_calls,
        done.cost_micros,
        done.info.prompt_version,
        done.model_answered,
        result.duration_sec,
        result.tss,
    )


def results_table(results: list[CaseResult]) -> str:
    lines = [
        "| case | result | outcome | turns | cost | prompt version | failed checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.name} | {'pass' if r.passed else 'FAIL'} | {r.outcome} | {r.turns} | "
            f"${r.cost_micros / 1e6:.4f} | {r.prompt_version[:12]} | "
            f"{', '.join(r.failed_checks) or '-'} |"
        )
    total = sum(r.cost_micros for r in results)
    passed = sum(r.passed for r in results)
    lines.append(f"\n{passed} of {len(results)} passed; total cost ${total / 1e6:.4f}")
    return "\n".join(lines)


def results_json(results: list[CaseResult]) -> str:
    rows: list[dict[str, Any]] = [
        {
            "case": r.name,
            "passed": r.passed,
            "outcome": r.outcome,
            "turns": r.turns,
            "tool_calls": r.tool_calls,
            "cost_micros": r.cost_micros,
            "prompt_version": r.prompt_version,
            "model_answered": r.model_answered,
            "duration_sec": r.duration_sec,
            "tss": r.tss,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in r.checks],
        }
        for r in results
    ]
    return json.dumps({"results": rows}, indent=1)
