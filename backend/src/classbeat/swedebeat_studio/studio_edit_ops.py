"""One user edit of a studio draft, by operation (ADR 0042 sections 3 to 5, #1391). No I/O; the
order of the checks and what is stored is ``studio_edit_calc``'s docstring."""

import copy
from typing import Any

from pydantic import ValidationError

from classbeat.agent.design import DesignResult
from classbeat.classes.user_edit_model import (
    AnchorBlock,
    ClearAll,
    Drag,
    Duration,
    Edit,
    LockBlock,
    Merge,
    Move,
    PinClear,
    PinConfirm,
    PinSet,
    RemoveAnchor,
    SetAttribute,
    Split,
    Unlock,
    UseAiValue,
)
from classbeat.design import workout_edit_policy as edits
from classbeat.design.edit_overlay_model import Anchor, Lock, OverlayPin, Override, Record
from classbeat.design.edit_overlay_policy import (
    LOCKED_BLOCK_CONFLICT,
    OverlayViolated,
    Placed,
    event_ms,
    moved,
    reapplied,
    set_end,
)
from classbeat.design.workout_edit_policy import Edited, EditRefused, blocks
from classbeat.plan_gates import PlanGates
from classbeat.swedebeat_studio import studio_edit_calc as calc
from classbeat.swedebeat_studio import studio_edit_records as recs
from classbeat.swedebeat_studio.studio_edit_calc import EditResult, EditState

PinEdit = PinSet | PinConfirm | PinClear
OverlayEdit = LockBlock | Unlock | AnchorBlock | RemoveAnchor | UseAiValue


def _others(state: EditState) -> list[Record]:
    return [r for r in state.overlay.records if not isinstance(r, OverlayPin)]


def _structural(
    state: EditState, edit: Edit, timing: frozenset[int], attrs: frozenset[int]
) -> Edited | None:
    base = state.content
    if isinstance(edit, Drag):
        return edits.drag(base, edit.block, edit.delta_sec, timing)
    if isinstance(edit, Move):
        return edits.move(base, edit.block, edit.direction, timing)
    if isinstance(edit, Split):
        return edits.split(base, edit.block, edit.offset_sec, timing)
    if isinstance(edit, Merge):
        return edits.merge(base, edit.block, timing, attrs)
    if isinstance(edit, SetAttribute):
        return edits.set_attribute(base, edit.block, edit.field, edit.value, attrs)
    if isinstance(edit, Duration):
        calc.class_length(state, edit.total_sec)
        return edits.duration(base, edit.total_sec, timing)
    return None


def _anchor(
    state: EditState,
    edit: AnchorBlock,
    content: dict[str, Any],
    placed: Placed,
    timing: frozenset[int],
) -> Placed:
    """Block ``edit.block``'s edge set onto the event, and the anchor recorded (in place)."""
    at = edit.block
    event = calc.event_of(state, edit.event.piece, edit.event.source_ms, edit.event.kind)
    when = event_ms(state.spec, state.analysed)(event)
    edge_at = at if edit.edge == "end" else at - 1
    if when is None or edge_at < 0:
        raise EditRefused("anchor_out_of_reach")
    if not set_end(content, edge_at, round(when / 1000) * 1000, timing):
        raise EditRefused("anchor_out_of_reach")
    kept = [p for p in placed if p not in recs.anchors(placed, at, edit.edge)]
    target = calc.target_of(content, at)
    return [*kept, (Anchor(type="anchor", target=target, edge=edit.edge, event=event), at)]


def _overlay_only(
    state: EditState, edit: OverlayEdit, placed: Placed, timing: frozenset[int]
) -> tuple[dict[str, Any], Placed]:
    """An edit of the overlay alone: its records, and the content it changes."""
    content = copy.deepcopy(state.content)
    at = edit.block
    if not 0 <= at < len(blocks(content)):
        raise EditRefused("invalid_input")
    if isinstance(edit, LockBlock):
        kept = [p for p in placed if p not in recs.locks(placed, at)]
        lock = Lock(type="lock", target=calc.target_of(content, at), scope=edit.scope)
        return content, [*kept, (lock, at)]
    if isinstance(edit, Unlock):
        return content, recs.without(placed, recs.locks(placed, at))
    if isinstance(edit, AnchorBlock):
        return content, _anchor(state, edit, content, placed, timing)
    if isinstance(edit, RemoveAnchor):
        return content, recs.without(placed, recs.anchors(placed, at, edit.edge))
    gone = recs.overrides(placed, at, edit.field)
    out = recs.without(placed, gone)
    record = gone[0][0]
    if isinstance(record, Override):
        recs.restore(content, record, at, timing)
    return content, out


def _edited(
    state: EditState, edit: Edit, timing: frozenset[int], attrs: frozenset[int]
) -> tuple[dict[str, Any], Placed, list[Record]]:
    """The edited workout before the check, its placed records and the loose ones."""
    placed, loose = calc.placed_of(state)
    if isinstance(edit, ClearAll):
        if not state.overlay.records:
            raise EditRefused("nothing_changed")
        return recs.restored(state.content, placed, timing), [], []
    if isinstance(edit, OverlayEdit):
        content, placed = _overlay_only(state, edit, placed, timing)
        return content, placed, loose
    structural = _structural(state, edit, timing, attrs)
    if structural is None:
        raise EditRefused("invalid_input")
    placed, loose = moved(_others(state), state.content, structural)
    if isinstance(edit, SetAttribute):
        now = calc.attribute_value(state.content, edit.block, edit.field)
        ai = recs.ai_value(placed, edit.block, edit.field, now)
        try:
            placed = calc.upsert(placed, structural.content, edit.block, edit.field, edit.value, ai)
        except ValidationError:
            raise EditRefused("invalid_input") from None
    return structural.content, placed, loose


def _workout(state: EditState, edit: Edit, gates: PlanGates | None) -> EditResult:
    timing, attrs = calc.locked(state)
    content, placed, loose = _edited(state, edit, timing, attrs)
    fitted, result, hints = calc.fit(state, content, state.spec, gates)
    if isinstance(edit, Drag):  # the dragged boundary takes the fitted end, checked again
        at = edit.block
        if set_end(content, at, blocks(fitted)[at].end_ms, timing):
            _, result, hints = calc.fit(state, content, state.spec, gates)
        ai = recs.ai_value(placed, at, "boundary", blocks(state.content)[at].end_ms)
        placed = calc.upsert(placed, content, at, "boundary", blocks(content)[at].end_ms, ai)
    content = calc.normal(content)
    records = [*calc.retargeted(placed, content), *loose, *state.overlay.pins()]
    return _result(state, content, state.spec, records, result, hints)


def _pins(state: EditState, edit: PinEdit) -> list[OverlayPin]:
    """The overlay's pins after ``edit``; a pin records the analysed file it was set against."""
    pins = list(state.overlay.pins())
    reference, occurrence = calc.piece_of(state, edit.piece)
    target = (reference, occurrence, edit.edge)
    others = [p for p in pins if p.target() != target]
    if isinstance(edit, PinClear):
        if len(others) == len(pins):
            raise EditRefused("nothing_changed")
        return others
    sha = state.analysed.get(reference)
    if sha is None:
        raise EditRefused("music_changed")
    made: dict[str, Any]
    if isinstance(edit, PinSet):
        made = {"requested_sec": round(edit.requested_sec, 2), "mode": edit.mode}
        made["confirmed"] = False
    else:
        cut = round(float(state.spec["tracks"][edit.piece][f"{edit.edge}_sec"]), 2)
        was = next((p for p in pins if p.target() == target), None)
        made = (
            {"requested_sec": cut, "mode": "exact"}
            if was is None
            else {"requested_sec": was.requested_sec, "mode": was.mode}
        )
        made |= {"confirmed": True, "confirmed_sec": cut, "confirmed_against": sha}
    common = {"type": "pin", "reference": reference, "occurrence": occurrence, "edge": edit.edge}
    try:
        return [*others, OverlayPin.model_validate({**common, **made, "set_against": sha})]
    except ValidationError:
        raise EditRefused("invalid_input") from None


def _audio(state: EditState, edit: PinEdit | ClearAll, gates: PlanGates | None) -> EditResult:
    """An audio edit, by amendment B's one order (``studio_edit_calc``'s docstring)."""
    timing, _ = calc.locked(state)
    placed, loose = calc.placed_of(state)
    base, records = state.content, _others(state)
    if isinstance(edit, ClearAll):
        pins: list[OverlayPin] = []
        base, records, loose = recs.restored(state.content, placed, timing), [], []
    else:
        pins = _pins(state, edit)
    spec, _ = calc.pinned_spec(state, pins)
    content = calc.remapped(state, base, spec)
    finder = event_ms(spec, state.analysed)
    try:
        applied = reapplied(content, base, records, event_ms=finder, remap=True)
    except OverlayViolated:
        raise EditRefused(LOCKED_BLOCK_CONFLICT) from None
    fitted, result, hints = calc.fit(
        state, applied.content, spec, gates, refused="workout_does_not_fit", before=state.spec
    )
    was, now = blocks(applied.content), blocks(fitted)
    for record, at in applied.placed:
        moved_by_fit = (was[at].start_ms, was[at].end_ms) != (now[at].start_ms, now[at].end_ms)
        if isinstance(record, Lock) and record.covers_timing() and moved_by_fit:
            raise EditRefused(LOCKED_BLOCK_CONFLICT)
    kept = [*applied.unapplied, *applied.orphaned]
    out = [*calc.retargeted(applied.placed, fitted), *kept, *[r for r in loose if r not in kept]]
    return _result(state, fitted, spec, [*out, *pins], result, hints)


def _result(
    state: EditState,
    content: dict[str, Any],
    spec: dict[str, Any],
    records: list[Record],
    result: DesignResult,
    hints: tuple[str, ...],
) -> EditResult:
    overlay = calc.overlay_of(records)
    same = content == state.content and spec == state.spec
    if same and overlay.stored() == state.overlay.stored():
        raise EditRefused("nothing_changed")
    unapplied, orphaned = recs.listed(content, spec, overlay, state.analysed)
    return EditResult(content, spec, overlay, unapplied, orphaned, hints, result)


def edited(state: EditState, edit: Edit, gates: PlanGates | None) -> EditResult:
    """One edit of ``state``, else ``EditRefused`` by name; a refusal stores nothing."""
    if isinstance(edit, PinSet | PinConfirm | PinClear):
        return _audio(state, edit, gates)
    if isinstance(edit, ClearAll) and state.overlay.pins():
        return _audio(state, edit, gates)
    return _workout(state, edit, gates)
