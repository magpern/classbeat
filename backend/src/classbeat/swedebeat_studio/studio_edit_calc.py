"""A user edit of a studio draft, computed (ADR 0042 sections 3 to 5, #1391). No I/O: the caller
(``classes.user_edit``) reads the draft, its proposal, choices and analyses and the plan gates.

``edited`` takes one operation (``classes.user_edit_model``) on the draft's workout, overlay or cut
pins and returns the new content, spec and overlay, else ``EditRefused`` by name; a refusal stores
nothing.

- **A workout or overlay edit** changes no composed total, so it skips the class-length steps: the
  operation (``workout_edit_policy``, the locks of the overlay honoured), the overlay's records
  carried exactly (``edit_overlay_policy.moved``), the automatic override (``set``: the field;
  ``drag``: ``boundary``), then ``agent.design.check`` on the draft's composed map and the plan
  gates (``plan_class_too_long``, ``plan_not_allowed``). The check validates; the stored workout
  keeps the instructor's own timing (the base is already a fit's output, and nothing moves
  silently), except that a dragged boundary takes the **fitted** end, which its override stores,
  and the result is checked again. ``duration`` is held to the class-length rule (B6,
  ``class_length_out_of_tolerance``) on its new total first. A ``set`` never changes the timing.
- **An audio edit** (``pin_set``, ``pin_confirm``, ``pin_clear``, and ``clear_all`` of a draft with
  pins) runs amendment B's **one order**: ``mix_choice_policy.pinned`` (``checked``, then
  ``class_length_out_of_tolerance``), B4a's remap of the workout onto the new timeline
  (``workout_does_not_fit``), the overlay re-applied by its fixed order (a timing-locked block that
  moved is ``locked_block_conflict``), the fit and the plan gates; the workout stored is the
  fitted one, as Apply's (``mix_apply_calc.fitted``), and a timing-locked block the fit moved is
  ``locked_block_conflict`` too. Its hints name a moved pin (``pin_moved``, the spec before the
  edit as ``before``).
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from pydantic import BaseModel, ValidationError

from classbeat.agent.design import DesignResult, check, within_plan
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.agent.studio_mix import compose_spec
from classbeat.design import edit_overlay_policy as overlay_policy
from classbeat.design.edit_overlay_model import (
    Anchor,
    EditOverlay,
    Event,
    Lock,
    OverlayPin,
    Override,
    Record,
    Target,
    of_records,
)
from classbeat.design.edit_overlay_policy import Placed, match
from classbeat.design.workout_edit_policy import EditRefused, blocks
from classbeat.plan_gates import PlanGates, max_draft_seconds
from classbeat.studio import mix_choice_policy, mix_remap_calc
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal
from classbeat.studio.mix_pin_policy import occurrences
from classbeat.swedebeat_studio import length_policy
from classbeat.swedebeat_studio.mix_apply_calc import StudioTracks, keyed, stored_spec

PLAN_TOO_LONG = "plan_class_too_long"
PLAN_NOT_ALLOWED = "plan_not_allowed"
MIX_NOT_APPLIED = "mix_not_applied"


@dataclass(frozen=True)
class EditState:
    """The draft as stored, and what its edits are checked with (the caller's reads)."""

    content: dict[str, Any]
    spec: dict[str, Any]  # the stored, hash-checked spec
    overlay: EditOverlay
    music: StudioTracks  # the approved list's tracks with the analyses the draft holds
    analysed: Mapping[str, str | None]  # each reference's analysed file's SHA-256
    proposal: Proposal
    choices: MixChoices | None  # its basis's (ADR 0042 section 8); None: not ``applied_form``
    map_version: int


@dataclass(frozen=True)
class EditResult:
    content: dict[str, Any]
    spec: dict[str, Any]
    overlay: EditOverlay
    unapplied: tuple[Record, ...] = ()
    orphaned: tuple[Anchor, ...] = ()
    hints: tuple[str, ...] = ()
    result: DesignResult | None = field(default=None, compare=False)
    map_version: int | None = field(default=None, compare=False)  # the composed map's


class Fit(NamedTuple):
    content: dict[str, Any]  # the fitted workout
    result: DesignResult
    hints: tuple[str, ...]
    map_version: int  # the composed map's ``version``


def fitted_content(content: dict[str, Any], fitted_spec: dict[str, Any]) -> dict[str, Any]:
    """The content with the fitted lengths and cadences (``revision.fitted_draft``'s rule)."""
    out = copy.deepcopy(content)
    for seg, fitted in zip(out["segments"], fitted_spec["segments"], strict=True):
        for interval, f in zip(seg["intervals"], fitted["intervals"], strict=True):
            interval["sec"] = int(f["sec"])
            interval["rpm"] = [int(f["rpm"][0]), int(f["rpm"][1])]
    return WorkoutDraft.model_validate(out).model_dump(mode="json", exclude_none=True)


def fit(
    state: EditState,
    content: dict[str, Any],
    spec: dict[str, Any],
    gates: PlanGates | None,
    *,
    refused: str = "invalid_edit",
    before: dict[str, Any] | None = None,
) -> Fit:
    """``agent.design.check`` of ``content`` on ``spec`` composed, then the plan gates."""
    references = state.music.references
    old = None if before is None else keyed(references, before)
    try:
        composed = compose_spec(state.music.tracks, keyed(references, spec), old)
        draft = WorkoutDraft.model_validate(content)
        result = check(draft, composed.map)
    except (ToolError, ValidationError):
        raise EditRefused(refused) from None
    try:
        within_plan(gates, result)
    except ToolError:
        too_long = gates is not None and result.duration_sec > max_draft_seconds(gates)
        raise EditRefused(PLAN_TOO_LONG if too_long else PLAN_NOT_ALLOWED) from None
    try:
        version = int(composed.map["version"])
        return Fit(fitted_content(content, result.spec), result, composed.hints, version)
    except (ValidationError, ValueError, KeyError, TypeError):
        raise EditRefused(refused) from None


def normal(content: dict[str, Any]) -> dict[str, Any]:
    """``content`` in the stored form, its own timing kept (a workout edit's result)."""
    try:
        return WorkoutDraft.model_validate(content).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        raise EditRefused("invalid_edit") from None


def placed_of(state: EditState) -> tuple[Placed, list[Record]]:
    """The non-pin records on the base's blocks, exactly; the rest as they are."""
    found = blocks(state.content)
    placed: Placed = []
    loose: list[Record] = []
    for record in state.overlay.records:
        if isinstance(record, OverlayPin):
            continue
        at = match(record.target, found)
        if at is None:
            loose.append(record)
        else:
            placed.append((record, at))
    return placed, loose


def locked(state: EditState) -> tuple[frozenset[int], frozenset[int]]:
    """The base's timing-locked and attribute-locked blocks."""
    placed, _ = placed_of(state)
    locks = [(r, n) for r, n in placed if isinstance(r, Lock)]
    return (
        frozenset(n for r, n in locks if r.covers_timing()),
        frozenset(n for r, n in locks if r.covers_attributes()),
    )


def attribute_value(content: dict[str, Any], at: int, name: str) -> object:
    interval = blocks(content)[at].interval
    return interval.get(name, "" if name == "cue" else None)


def target_of(content: dict[str, Any], at: int) -> Target:
    block = blocks(content)[at]
    return Target(segment_kind=block.kind, span=[block.start_ms, block.end_ms])  # pyright: ignore[reportArgumentType]


def upsert(
    placed: Placed, content: dict[str, Any], at: int, name: str, value: object, ai_value: object
) -> Placed:
    """``placed`` with block ``at``'s override of ``name`` set to ``value``: an earlier override's
    ``ai_value_at_set`` kept; none when the value is the AI's again."""
    kept: Placed = []
    for record, n in placed:
        if isinstance(record, Override) and n == at and record.field == name:
            ai_value = record.ai_value_at_set
        else:
            kept.append((record, n))
    if value == ai_value:
        return kept
    override = Override(
        type="override",
        target=target_of(content, at),
        field=name,  # pyright: ignore[reportArgumentType]
        value=value,
        ai_value_at_set=ai_value,
    )
    return [*kept, (override, at)]


def overlay_of(records: list[Record]) -> EditOverlay:
    try:
        return of_records(records)
    except (ValidationError, ValueError):
        raise EditRefused("overlay_too_large") from None


def retargeted(placed: Placed, fitted: dict[str, Any]) -> list[Record]:
    """``edit_overlay_policy.retargeted``, without the boundary overrides that are the AI's end
    again (the fit put the block back)."""

    def kept(record: Record) -> bool:
        if isinstance(record, Override) and record.field == "boundary":
            return record.value != record.ai_value_at_set
        return True

    return [r for r in overlay_policy.retargeted(placed, fitted) if kept(r)]


def piece_of(state: EditState, piece: int) -> tuple[str, int]:
    at = occurrences(state.spec)
    if not 0 <= piece < len(at):
        raise EditRefused("invalid_input")
    return at[piece]


def event_of(state: EditState, piece: int, source_ms: int, kind: str) -> Event:
    reference, occurrence = piece_of(state, piece)
    sha = state.analysed.get(reference)
    if sha is None:
        raise EditRefused("music_changed")
    return Event(
        reference=reference,
        occurrence=occurrence,
        sha256=sha,
        source_ms=source_ms,
        kind=kind,  # pyright: ignore[reportArgumentType]
        map_version=state.map_version,
    )


def class_length(state: EditState, total: int) -> None:
    if not length_policy.within(total, state.proposal.target_sec):
        raise EditRefused(mix_choice_policy.OUT_OF_TOLERANCE)


def pinned_spec(state: EditState, pins: list[OverlayPin]) -> tuple[dict[str, Any], tuple[Any, ...]]:
    """Steps 1 and 2 of the audio-edit order: the derived spec with ``pins``, gated."""
    if state.choices is None:
        raise EditRefused(MIX_NOT_APPLIED)  # its spec is no ``applied`` of its choices (B9)
    try:
        found = mix_choice_policy.pinned(state.proposal, state.music.facts(), state.choices, pins)
        return stored_spec(found.spec), (*found.unapplied, *found.dropped)
    except MixChoiceRefused as exc:
        raise EditRefused(exc.reason) from None
    except (ValueError, KeyError, TypeError):
        raise EditRefused("internal") from None


def remapped(state: EditState, content: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Step 3's remap: ``content`` (the base's workout) moved onto ``spec``'s timeline (B4a)."""
    out = copy.deepcopy(content)
    flat = [i for s in out["segments"] for i in s["intervals"]]
    try:
        lengths = mix_remap_calc.remapped(state.spec, spec, [int(i["sec"]) for i in flat])
    except MixChoiceRefused as exc:
        raise EditRefused(exc.reason) from None
    for interval, sec in zip(flat, lengths, strict=True):
        interval["sec"] = sec
    return out


def stored(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True)
