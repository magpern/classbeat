"""The user's mix choices turned into a new studio draft, without a model (ADR 0036 amendment B, B4
and B4a, #1369). No I/O: the callers read the proposal, the analyses and the plan gates.

**The one order of the checks** (B4; the preview runs steps 1 and 2, the job all of them, so the
first reason is the same in both):

1. ``derived``: ``mix_choice_policy.applied`` on the **proposal** (never on an applied draft):
   the choices' ``invalid_input``, ``mix_plan_policy.checked``'s reasons, then the class length
   (``class_length_out_of_tolerance``);
2. ``fitted``: the proposal's stored, fitted workout moved onto the derived timeline by
   ``mix_remap_calc.remapped`` (B4a steps 1 to 5; a squeezed interval is
   ``workout_does_not_fit``); the derived spec composed (``agent.studio_mix.compose``) and the
   moved workout checked on that map by ``agent.design.check`` (the fit within 6 s,
   ``workout.validate``, the timing gate; a refusal is ``workout_does_not_fit``); then the plan
   gates as the studio design runs them (``agent.design.within_plan``): a fitted class over
   ``plan_gates.max_draft_seconds`` is ``plan_class_too_long``, any other plan problem (a game
   or an FTP test the plan does not include) ``plan_not_allowed``.

Every refusal is ``MixChoiceRefused`` with its name; a ``ValueError`` is a broken invariant (the
stored proposal no longer reads, or the composed plan is not the derived spec), never a reason to
show.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from pydantic import ValidationError

from classbeat.agent.design import DesignResult, check, within_plan
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.agent.studio_mix import Composed, StudioTrack, compose
from classbeat.agent.studio_schemas import MixInput
from classbeat.plan_gates import PlanGates, max_draft_seconds
from classbeat.studio import mix_choice_policy, mix_remap_calc
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_spec_model import MixSpec

DOES_NOT_FIT: Final = mix_remap_calc.DOES_NOT_FIT
PLAN_TOO_LONG: Final = "plan_class_too_long"
PLAN_NOT_ALLOWED: Final = "plan_not_allowed"


class StudioTracks(Protocol):
    """The approved list's tracks with their stored analyses, in the list's order (what
    ``swedebeat_studio.draft.StudioMusic`` holds; a protocol, so this module imports no I/O)."""

    @property
    def references(self) -> Sequence[str]: ...

    @property
    def tracks(self) -> Sequence[StudioTrack]: ...

    def facts(self) -> Mapping[str, TrackFacts]: ...


@dataclass(frozen=True)
class Fitted:
    """The derived spec, its composed map and the moved workout checked on it."""

    spec: dict[str, Any]
    composed: Composed
    result: DesignResult


def derived(proposal: Proposal, music: StudioTracks, choices: MixChoices) -> dict[str, Any]:
    """Step 1: the derived, checked spec, else ``MixChoiceRefused`` by name."""
    return mix_choice_policy.applied(proposal, music.facts(), choices)


def stored_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """A spec in its stored form (``MixSpec``), the one the hash and the digest cover."""
    return MixSpec.model_validate(spec).model_dump(mode="json")


def plan_of(references: Sequence[str], spec: dict[str, Any]) -> dict[str, Any]:
    """A spec by the list's indexes (``agent.studio_mix``'s plan); ``ValueError`` for a reference
    off the list (the derived spec names only the proposal's, so that is a broken invariant)."""
    index = {r: n for n, r in enumerate(references)}
    stored = stored_spec(spec)
    if any(t["reference"] not in index for t in stored["tracks"]):
        raise ValueError("a piece is not a track of the approved list")
    return {
        "tracks": [
            {"track": index[t["reference"]], "start_sec": t["start_sec"], "end_sec": t["end_sec"]}
            for t in stored["tracks"]
        ],
        "crossfades": list(stored["crossfades"]),
    }


def spec_digest(spec: dict[str, Any]) -> str:
    """The hex SHA-256 of a spec's stored form as canonical JSON (B10: the screen offers Approve
    only when the current draft's digest equals the preview's)."""
    text = json.dumps(stored_spec(spec), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _intervals(draft: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for s in draft["segments"] for i in s["intervals"]]


def moved(
    content: dict[str, Any], proposal_spec: dict[str, Any], spec: dict[str, Any]
) -> WorkoutDraft:
    """B4a steps 1 to 5: the proposal's workout with each interval's ``sec`` remapped onto the
    derived timeline; a length the draft schema refuses (an FTP test whose length moved) is
    ``workout_does_not_fit`` too."""
    try:
        draft = WorkoutDraft.model_validate(content).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        raise ValueError("the stored proposal no longer reads as a draft") from None
    intervals = _intervals(draft)
    lengths = mix_remap_calc.remapped(proposal_spec, spec, [int(i["sec"]) for i in intervals])
    for interval, sec in zip(intervals, lengths, strict=True):
        interval["sec"] = sec
    try:
        return WorkoutDraft.model_validate(draft)
    except ValidationError:
        raise MixChoiceRefused(DOES_NOT_FIT, "the moved workout is not a valid draft") from None


def fitted(
    content: dict[str, Any],
    proposal_spec: dict[str, Any],
    spec: dict[str, Any],
    music: StudioTracks,
    gates: PlanGates | None,
) -> Fitted:
    """Step 2 (module docstring), on the derived spec ``spec`` that ``derived`` returned."""
    workout = moved(content, proposal_spec, spec)
    plan = plan_of(music.references, spec)
    try:
        composed = compose(music.tracks, MixInput.model_validate(plan))
    except (ToolError, ValidationError):
        raise ValueError("the derived spec does not compose") from None
    if composed.plan != plan:
        raise ValueError("the composed plan is not the derived spec")
    try:
        result = check(workout, composed.map)
    except ToolError as refused:
        raise MixChoiceRefused(DOES_NOT_FIT, "; ".join(refused.messages)) from None
    try:
        within_plan(gates, result)
    except ToolError as refused:
        too_long = gates is not None and result.duration_sec > max_draft_seconds(gates)
        reason = PLAN_TOO_LONG if too_long else PLAN_NOT_ALLOWED
        raise MixChoiceRefused(reason, "; ".join(refused.messages)) from None
    return Fitted(spec, composed, result)
