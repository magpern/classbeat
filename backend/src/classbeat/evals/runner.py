"""One case through the real design or revise stage (ADR 0006 section 8).

``run_case(case, provider, config)`` builds the case's synthetic music, runs
``classbeat.agent.run_stage`` with the given provider on an in-memory host (no database, no job),
and runs the checks on the draft it submitted. A run that fails (a limit, a refusal, a provider
error) is a failed case whose outcome is the failure category. A chained revise case
(``ReviseSpec.interpret``, ``revise_chain``) reads the request with the interpret stage first, as a
revise job does, and the revise stage gets the layered brief.

``CaseResult`` and the results formats are in ``classbeat.evals.results``; this module
re-exports their names.
"""

import math
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from classbeat.agent import DesignContext, DesignResult, run_stage
from classbeat.agent.loop import AgentConfig, Counters, RunInfo
from classbeat.agent.provider import ModelProvider
from classbeat.agent.revise import ReviseContext, ReviseResult, SongsRequested
from classbeat.agent.running import design_stage_for, revise_stage_for
from classbeat.agent.soundless import (
    SoundlessContext,
    SoundlessReviseContext,
    soundless_design_stage,
    soundless_revise_stage,
)
from classbeat.agent.stage import Stage
from classbeat.design.sport_rules import rules_for
from classbeat.evals.cases import Case
from classbeat.evals.checks import Check, has_zone7, run_checks, tss_bounds_for
from classbeat.evals.music import build_music
from classbeat.evals.results import RESULTS_FORMAT as RESULTS_FORMAT
from classbeat.evals.results import RESULTS_FORMAT_SUITE as RESULTS_FORMAT_SUITE
from classbeat.evals.results import CaseResult as CaseResult
from classbeat.evals.results import SuiteHeader as SuiteHeader
from classbeat.evals.results import class_shape as class_shape
from classbeat.evals.results import failed_check_lines as failed_check_lines
from classbeat.evals.results import result_row as result_row
from classbeat.evals.results import results_json as results_json
from classbeat.evals.results import results_table as results_table
from classbeat.evals.results import song_suite_row as song_suite_row
from classbeat.evals.results import suite_results as suite_results
from classbeat.evals.results import suite_results_json as suite_results_json
from classbeat.evals.results import suite_row as suite_row
from classbeat.evals.revise_chain import ReadFailed, counted, read_revision
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


def _soundless_stage(case: Case) -> tuple[Stage, str]:
    """The soundless ``design`` or ``revise`` stage of the case and its first message."""
    if case.revise is None:
        design = SoundlessContext(case.brief)
        return soundless_design_stage(design), design.first_message()
    revise = SoundlessReviseContext(
        case.brief, case.revise.base, tuple(case.revise.earlier_requests), case.revise.request
    )
    return soundless_revise_stage(revise), revise.first_message()


async def run_case(
    case: Case,
    provider: ModelProvider,
    config: AgentConfig,
    *,
    repeat: int = 0,
    clock: Callable[[], float] = time.monotonic,
    zone7_tss_margin: float = 0.0,
) -> CaseResult:
    """``zone7_tss_margin``: the suite's (its manifest); the smoke set runs with 0. A chained
    revise case reads its request first; the revise run is then made on the layered brief, and
    the result counts both runs."""
    if case.revise is None or not case.revise.interpret:
        return await _run_stage(case, provider, config, repeat, clock, zone7_tss_margin)
    host, started = EvalHost(), clock()
    try:
        read = await read_revision(case, provider, config, host)
    except ReadFailed as failure:
        c = failure.spent
        return CaseResult(
            case.name, failure.outcome, turns=c.turns, tool_calls=c.tool_calls,
            cost_micros=c.cost_micros, model_answered=c.model_answered, model=config.model,
            effort=config.effort, repeat=repeat, corrections=c.corrections,
            wall_sec=clock() - started, usage=c.usage,
        )  # fmt: skip
    layered = case.model_copy(update={"brief": read.brief})
    done = await _run_stage(layered, provider, config, repeat, clock, zone7_tss_margin)
    return replace(counted(done, read.spent), wall_sec=clock() - started)


async def _run_stage(
    case: Case,
    provider: ModelProvider,
    config: AgentConfig,
    repeat: int,
    clock: Callable[[], float],
    zone7_tss_margin: float,
) -> CaseResult:
    # A soundless case (ADR 0007 amendment of 2026-11-03, issue #531) is given the soundless stages
    # as a soundless job gets them (``design.kind._run_soundless``, ``design.revise``): the brief
    # and no music, no rules section, no music map, no gates.
    music_sec = math.inf  # a soundless class is not bounded by any music
    music_map: dict[str, Any] | None = None
    if case.soundless:
        stage, first = _soundless_stage(case)
    else:
        music = build_music(case.music)
        # As in production (ADR 0017 section 3): the rules of the brief's profile, if one applies,
        # are in the model's input and in its tool results. Nothing is dismissed on a design run.
        # A running case gets the running profile, prompt and schema (ADR 0018), as a running job.
        rules = rules_for(case.brief)
        ctx = DesignContext.build(case.brief, music, rules)
        stage, first = design_stage_for(ctx), ctx.first_message()
        music_sec, music_map = float(ctx.map["duration"]), ctx.map
        if case.revise is not None:
            revise = ReviseContext.build(
                case.brief,
                music,
                case.revise.base,
                tuple(case.revise.earlier_requests),
                case.revise.request,
                music_changed=False,
                rules=rules,
            )
            stage, first = revise_stage_for(revise), revise.first_message()
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
    wants_songs = case.revise is not None and case.revise.outcome == "songs_requested"
    if isinstance(output, SongsRequested) or wants_songs:
        # No draft to check: the case passes if, and only if, it was a request for other songs.
        got = isinstance(output, SongsRequested)
        limits = config.limits
        max_cost = limits.job_cost_micros
        checks = [
            Check(
                "points_to_change_songs",
                got == wants_songs,
                "the stage ended with songs_requested" if got else "the stage submitted a draft",
            ),
            # The suite's rules read these two on every run; a run with no draft says no text.
            Check("forbidden_text", True, "no text to check"),
            Check(
                "limits",
                done.turns <= limits.max_turns
                and done.tool_calls <= limits.max_tool_calls
                and done.cost_micros <= max_cost,
                f"{done.turns} turns of {limits.max_turns}, {done.tool_calls} tool calls of "
                f"{limits.max_tool_calls}, cost {done.cost_micros} of {max_cost} micro-dollars",
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
            wall_sec=wall,
            usage=done.usage,
        )
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
            music_sec,
            note,
            zone7_tss_margin=zone7_tss_margin,
            music_map=music_map,
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
        tss_bounds=None  # no running TSS bound: the running load model is not calibrated
        if case.brief.sport == "running"
        else tss_bounds_for(case, result.duration_sec, zone7=zone7, zone7_margin=zone7_tss_margin),
        changes_on_music=str(result.quality.get("changes_on_music", "")),
        timeline=class_shape(result.timeline),
        tss_approximate=zone7,
    )
