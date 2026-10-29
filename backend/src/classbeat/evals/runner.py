"""One smoke case through the real design stage (ADR 0006 section 8).

``run_case(case, provider, config)`` builds the case's synthetic music, runs
``classbeat.agent.run_stage`` with the given provider on an in-memory host (no database, no job),
and runs the checks on the draft it submitted. A run that fails (a limit, a refusal, a provider
error) is a failed case whose outcome is the failure category.

``CaseResult`` holds numbers, check names and details (numbers and rule names), the category and
the shape of the class (per segment its name and kind, per interval its length, zone and the index
of the track under it) only: no cues, no description, no song titles, no ids. ``results_table`` and
``results_json`` are what the owner pastes into a PR; ``classbeat.evals.compare`` reads the JSON.
"""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from classbeat.agent import DesignContext, DesignResult, design_stage, run_stage
from classbeat.agent.loop import AgentConfig, Counters, RunInfo
from classbeat.agent.provider import ModelProvider, Usage
from classbeat.agent.revise import ReviseContext, ReviseResult, revise_stage
from classbeat.evals.cases import Case
from classbeat.evals.checks import Check, run_checks, tss_bounds_for
from classbeat.evals.music import build_music
from classbeat.jobs.errors import JobFailure

RESULTS_FORMAT = 1  # of ``results_json``; ``classbeat.evals.compare`` refuses another


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
    # What a comparison of models needs (issue #134); the zero values are a run that never got
    # that far.
    model: str = ""  # the model asked for (``model_answered`` is the one that answered)
    effort: str = ""
    repeat: int = 0  # 0-based index of this run of the case
    corrections: int = 0
    wall_sec: float = 0.0
    usage: Usage = field(default_factory=Usage)
    tss_bounds: tuple[float, float] | None = None
    changes_on_music: str = ""  # "x of y" interval starts on the music, as quality.report says
    timeline: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    @property
    def passed(self) -> bool:
        return self.outcome == "submitted" and bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


def class_shape(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The timeline cut down to what is drawn: segment name and kind; per interval the seconds, the
    zone, whether standing and the index of the track under it. No cues, no song titles."""
    return [
        {
            "name": str(seg["name"])[:80],
            "kind": str(seg["kind"]),
            "intervals": [
                {
                    "sec": int(i["sec"]),
                    "zone": int(i["zone"]),
                    "standing": bool(i["standing"]),
                    "track": int(i["track"]),
                }
                for i in seg["intervals"]
            ],
        }
        for seg in timeline
    ]


async def run_case(
    case: Case,
    provider: ModelProvider,
    config: AgentConfig,
    *,
    repeat: int = 0,
    clock: Callable[[], float] = time.monotonic,
) -> CaseResult:
    music = build_music(case.music)
    ctx = DesignContext.build(case.brief, music)
    stage, first = design_stage(ctx), ctx.first_message()
    if case.revise is not None:
        revise = ReviseContext.build(
            case.brief,
            music,
            case.revise.base,
            tuple(case.revise.earlier_requests),
            case.revise.request,
            music_changed=False,
        )
        stage, first = revise_stage(revise), revise.first_message()
    host = EvalHost()
    started = clock()

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
            model=config.model,
            effort=config.effort,
            repeat=repeat,
            corrections=c.corrections,
            wall_sec=clock() - started,
            usage=c.usage,
        )

    try:
        done = await run_stage(host, provider, stage, first, config)
    except JobFailure as failure:
        return failed(failure.category)
    except Exception as exc:  # the class name only: a message could hold request data
        return failed(f"error:{type(exc).__name__}")
    wall = clock() - started
    output, note = done.output, ""
    if isinstance(output, ReviseResult):
        output, note = output.result, output.note
    if not isinstance(output, DesignResult):
        return failed("error:BadOutput")
    result = output
    try:
        checks = run_checks(case, result, done, config.limits, float(ctx.map["duration"]), note)
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
        model=config.model,
        effort=config.effort,
        repeat=repeat,
        corrections=done.corrections,
        wall_sec=wall,
        usage=done.usage,
        tss_bounds=tss_bounds_for(case, result.duration_sec),
        changes_on_music=str(result.quality.get("changes_on_music", "")),
        timeline=class_shape(result.timeline),
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


def result_row(r: CaseResult) -> dict[str, Any]:
    return {
        "case": r.name,
        "repeat": r.repeat,
        "model": r.model,
        "effort": r.effort,
        "model_answered": r.model_answered,
        "prompt_version": r.prompt_version,
        "passed": r.passed,
        "outcome": r.outcome,
        "turns": r.turns,
        "tool_calls": r.tool_calls,
        "corrections": r.corrections,
        "cost_micros": r.cost_micros,
        "wall_sec": round(r.wall_sec, 2),
        "input_tokens": r.usage.input_tokens,
        "output_tokens": r.usage.output_tokens,
        "cache_read_tokens": r.usage.cache_read_tokens,
        "cache_write_tokens": r.usage.cache_write_tokens,
        "duration_sec": r.duration_sec,
        "tss": r.tss,
        "tss_bounds": list(r.tss_bounds) if r.tss_bounds else None,
        "changes_on_music": r.changes_on_music,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in r.checks],
        "timeline": r.timeline,
    }


def results_json(results: list[CaseResult]) -> str:
    return json.dumps(
        {"format": RESULTS_FORMAT, "results": [result_row(r) for r in results]}, indent=1
    )


def failed_check_lines(r: CaseResult) -> list[str]:
    """One line per failed check with its detail (numbers and rule names only)."""
    return [f"  {r.name} failed {c.name}: {c.detail}" for c in r.checks if not c.ok]
