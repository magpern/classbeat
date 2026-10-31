"""One smoke case through the real design stage (ADR 0006 section 8).

``run_case(case, provider, config)`` builds the case's synthetic music, runs
``classbeat.agent.run_stage`` with the given provider on an in-memory host (no database, no job),
and runs the checks on the draft it submitted. A run that fails (a limit, a refusal, a provider
error) is a failed case whose outcome is the failure category.

``CaseResult`` holds numbers, check names and details (numbers and rule names), the category and
the shape of the class (per segment its name and kind, per interval its length, zone and the index
of the track under it) only: no cues, no description, no song titles, no ids. ``results_table`` and
``results_json`` are what the owner pastes into a PR; ``classbeat.evals.compare`` reads the JSON.

The suite (ADR 0012 section 4.2) writes **format 2**: ``suite_results`` is one file per run of the
suite, the ``SuiteHeader`` (suite version and digest, the choice, the prompt version of each stage,
the classbeat, mowl and price versions, the repeat count, the times and the provider kind built),
then per run of a case the format-1 row plus its ``category`` (``suite_row``) or a song run's row
(``song_suite_row``). Same rule: numbers and names only. ``smoke --live`` keeps format 1.
"""

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from classbeat import __version__ as _classbeat_version
from classbeat.agent import DesignContext, DesignResult, design_stage, run_stage
from classbeat.agent.loop import AgentConfig, Counters, RunInfo
from classbeat.agent.prices import PRICES_VERSION
from classbeat.agent.provider import ModelProvider, Usage
from classbeat.agent.records import mowl_version as _mowl_version
from classbeat.agent.revise import ReviseContext, ReviseResult, revise_stage
from classbeat.evals.cases import Case
from classbeat.evals.checks import Check, has_zone7, run_checks, tss_bounds_for
from classbeat.evals.music import build_music
from classbeat.jobs.errors import JobFailure

if TYPE_CHECKING:
    from classbeat.evals.pick import PickCaseResult

RESULTS_FORMAT = 1  # of ``results_json`` (``smoke --live``); ``classbeat.evals.compare`` reads it
RESULTS_FORMAT_SUITE = 2  # of ``suite_results`` (ADR 0012 section 4.2); ``evals.gate`` reads it


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
    tss_approximate: bool = False  # a zone 7 interval: its TSS weight is a guess (ADR 0012 s. 7)

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
    zone7_tss_margin: float = 0.0,
) -> CaseResult:
    """``zone7_tss_margin``: the suite's (its manifest); the smoke set runs with 0."""
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
        checks = run_checks(
            case,
            result,
            done,
            config.limits,
            float(ctx.map["duration"]),
            note,
            zone7_tss_margin=zone7_tss_margin,
        )
        zone7 = has_zone7(result.spec)
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
        tss_bounds=tss_bounds_for(
            case, result.duration_sec, zone7=zone7, zone7_margin=zone7_tss_margin
        ),
        changes_on_music=str(result.quality.get("changes_on_music", "")),
        timeline=class_shape(result.timeline),
        tss_approximate=zone7,
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


@dataclass(frozen=True)
class SuiteHeader:
    """The header of a suite results file (ADR 0012 section 4.2). ``provider``, ``model`` and
    ``effort`` are the catalog choice the run was given; ``provider_kind`` is the ``name`` of the
    provider actually built. Versions and times only: no key, no account, no model text."""

    suite_version: int
    manifest_digest: str
    provider: str
    model: str
    effort: str
    prompt_versions: Mapping[str, str]  # stage -> prompt_version
    repeat: int
    started_at: datetime
    ended_at: datetime
    provider_kind: str
    classbeat_version: str = _classbeat_version
    mowl_version: str = field(default_factory=_mowl_version)
    prices_version: str = PRICES_VERSION


def _utc(at: datetime) -> str:
    if at.tzinfo is None:
        raise ValueError("a results time is timezone-aware")
    return at.astimezone(UTC).isoformat()


def suite_row(r: CaseResult, category: str) -> dict[str, Any]:
    """A design or revise run in format 2: the format-1 row plus its category, the zone 7 flag
    and the scores (none for a design run)."""
    return {
        **result_row(r),
        "category": category,
        "tss_approximate": r.tss_approximate,
        "scores": {},
    }


def song_suite_row(
    r: "PickCaseResult", category: str, *, repeat: int, model: str, effort: str
) -> dict[str, Any]:
    """A song run (``pick_pool`` then ``choose_songs``) in format 2: numbers, check names and
    details (roles and seconds), the prompt version of each stage. No title, artist or id."""
    return {
        "case": r.name,
        "repeat": repeat,
        "category": category,
        "model": model,
        "effort": effort,
        "prompt_versions": dict(r.prompt_versions),
        "passed": r.passed,
        "outcome": r.outcome,
        "turns": r.turns,
        "tool_calls": r.tool_calls,
        "cost_micros": r.cost_micros,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in r.checks],
        "scores": dict(r.scores),
    }


def suite_results(header: SuiteHeader, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One file per run of the suite: the header of section 4.2, then the runs."""
    return {
        "results_format": RESULTS_FORMAT_SUITE,
        "suite_version": header.suite_version,
        "manifest_digest": header.manifest_digest,
        "provider": header.provider,
        "model": header.model,
        "effort": header.effort,
        "prompt_versions": dict(sorted(header.prompt_versions.items())),
        "classbeat_version": header.classbeat_version,
        "mowl_version": header.mowl_version,
        "prices_version": header.prices_version,
        "repeat": header.repeat,
        "started_at": _utc(header.started_at),
        "ended_at": _utc(header.ended_at),
        "provider_kind": header.provider_kind,
        "results": [dict(row) for row in rows],
    }


def suite_results_json(header: SuiteHeader, rows: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(suite_results(header, rows), indent=1, allow_nan=False)


def failed_check_lines(r: CaseResult) -> list[str]:
    """One line per failed check with its detail (numbers and rule names only)."""
    return [f"  {r.name} failed {c.name}: {c.detail}" for c in r.checks if not c.ok]
