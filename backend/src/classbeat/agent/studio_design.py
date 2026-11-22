"""The studio's ``design`` and ``revise`` stages (ADR 0036 section 4.4 and 4.5, SR C5): the model
designs the workout **and** the mix (order, cuts, crossfades) over the approved tracks' analyses.

- ``get_music()``: the tracks (``studio_mix.music_summary``); ``get_music(track=i)``: track ``i``
  with its sections (``out_of_range`` outside the list).
- ``preview_draft(draft, mix)`` and ``submit_draft(draft, mix[, note])``: the mix checked by
  ``mix_plan_policy`` and composed (``studio_mix.compose``: ``invalid_mix`` with the named reason),
  then the design stage's own checks on the composed map (``design.check``: ``workout.validate``,
  ``fit_to_music``, the plan's gates, the stated values). The answer adds the plan as the backend
  snapped it and the total it computed. On success the stage ends with a ``StudioResult``.
- A revise starts from the base draft and the base mix (by index) and the instructor's requests
  (``studio_prompt.revise_message``), and a re-plan also from the user's mix choices
  (``StudioBase.choices``, stated first by index, ADR 0036 B6); its ``submit_draft`` takes a
  ``note`` (shown as plain text, never read back), and it has no ``songs_requested`` exit: Change
  songs is the studio's own pick.

Own system prompts and tool schemas, so each stage has its own ``prompt_version``. Pure: no
network, no credentials, no database, no catalogue reference.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from mowl.workout import mmss

from classbeat.agent.brief import Brief
from classbeat.agent.design import DesignResult, check, within_plan, within_request
from classbeat.agent.explicit_input import ExplicitGate, with_explicit
from classbeat.agent.guidance import Guidance, rules_result
from classbeat.agent.schemas import MusicInput
from classbeat.agent.stage import Stage, Tool, ToolAnswer, ToolError
from classbeat.agent.studio_mix import (
    Composed,
    StudioTrack,
    by_list_index,
    compose,
    plan_for_model,
    warnings_by_list_index,
)
from classbeat.agent.studio_mix import music_summary as summarize
from classbeat.agent.studio_mix import track_detail as detail
from classbeat.agent.studio_prompt import (
    DESIGN_SYSTEM,
    GET_MUSIC,
    PREVIEW,
    REVISE_SYSTEM,
    SUBMIT,
    SUBMIT_REVISE,
    first_message,
    revise_message,
)
from classbeat.agent.studio_schemas import StudioDraftInput, StudioReviseInput
from classbeat.plan_gates import PlanGates
from classbeat.studio.mix_choice_model import MixChoices

DESIGN_STAGE: Final = "studio_design"
REVISE_STAGE: Final = "studio_revise"
FINAL_TOOL: Final = "submit_draft"


@dataclass(frozen=True)
class StudioResult:
    """The checked draft on the composed map, the plan by index and the map itself."""

    result: DesignResult
    composed: Composed
    note: str = ""


@dataclass(frozen=True)
class StudioBase:
    """A revise's starting point: the base draft (``content``) and its mix by index; ``choices``
    for a re-plan (ADR 0036 B6), already checked against the list."""

    draft: dict[str, Any]
    plan: dict[str, Any]
    earlier_requests: tuple[str, ...]
    request: str
    choices: MixChoices | None = None


@dataclass(frozen=True)
class StudioContext:
    brief: Brief
    tracks: tuple[StudioTrack, ...]
    rules: Guidance | None = None
    gates: PlanGates | None = None
    base: StudioBase | None = None  # set for a revise

    def first_message(self) -> str:
        rules = self.rules.section() if self.rules else None
        music = summarize(self.tracks)
        if self.base is None:
            return with_explicit(first_message(self.brief, music, rules), self.brief)
        b = self.base
        text = revise_message(
            self.brief,
            music,
            b.draft,
            b.plan,
            b.earlier_requests,
            b.request,
            rules=rules,
            choices=b.choices,
        )
        return with_explicit(text, self.brief)


def checked(ctx: StudioContext, given: StudioDraftInput) -> tuple[DesignResult, Composed]:
    composed = compose(ctx.tracks, given.mix)
    return within_plan(ctx.gates, check(given.draft, composed.map)), composed


def _answer(result: DesignResult, composed: Composed, ctx: StudioContext) -> dict[str, Any]:
    return {
        "duration": mmss(result.duration_sec),
        "duration_sec": result.duration_sec,
        "tss": result.tss,
        "tss_approximate": result.tss_approximate,
        "mix": plan_for_model(composed),
        "quality": result.quality,
        "warnings": warnings_by_list_index(composed, result.warnings),
        **rules_result(ctx.rules, result.spec),
    }


def studio_stage(ctx: StudioContext) -> Stage:
    """The design stage, or with ``ctx.base`` the revise stage."""
    if ctx.brief.sport != "cycling":
        raise ValueError("the studio designs cycling classes only")
    gate = ExplicitGate(ctx.brief)
    tracks: Sequence[StudioTrack] = ctx.tracks

    def get_music(args: MusicInput) -> ToolAnswer:
        if args.track is None:
            return ToolAnswer({"music": summarize(tracks)})
        if not 0 <= args.track < len(tracks):
            raise ToolError("out_of_range", [f"track must be an index from 0 to {len(tracks) - 1}"])
        return ToolAnswer({"track": detail(tracks, args.track)})

    def preview_draft(args: StudioDraftInput) -> ToolAnswer:
        result, composed = checked(ctx, args)
        timeline = by_list_index(composed, result.timeline)
        return ToolAnswer({**_answer(result, composed, ctx), "timeline": timeline})

    def submit(args: StudioDraftInput, note: str) -> ToolAnswer:
        made: list[Composed] = []

        def build() -> DesignResult:
            result, composed = checked(ctx, args)
            made.append(composed)
            return result

        result = within_request(gate, build)
        composed = made[-1]
        answer = {"accepted": True, **_answer(result, composed, ctx)}
        answer.pop("duration_sec")
        return ToolAnswer(answer, output=StudioResult(result, composed, note))

    def submit_draft(args: StudioDraftInput) -> ToolAnswer:
        return submit(args, "")

    def submit_revise(args: StudioReviseInput) -> ToolAnswer:
        return submit(args, args.note)

    revise = ctx.base is not None
    final = (
        Tool(FINAL_TOOL, "compute", SUBMIT_REVISE, StudioReviseInput, submit_revise)
        if revise
        else Tool(FINAL_TOOL, "compute", SUBMIT, StudioDraftInput, submit_draft)
    )
    return Stage(
        name=REVISE_STAGE if revise else DESIGN_STAGE,
        system=REVISE_SYSTEM if revise else DESIGN_SYSTEM,
        tools=(
            Tool("get_music", "read", GET_MUSIC, MusicInput, get_music),
            Tool("preview_draft", "compute", PREVIEW, StudioDraftInput, preview_draft),
            final,
        ),
        final_tool=FINAL_TOOL,
        bind_corrections=gate.bind,
    )


__all__ = ["StudioBase", "StudioContext", "StudioResult", "studio_stage"]
