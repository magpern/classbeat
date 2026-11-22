"""The edit overlay through a studio revise, an AI regeneration (ADR 0042 sections 3 and 8, #1391).
No I/O: ``revise_kind`` reads the base's overlay and passes these to the stage
(``agent.studio_design.StudioBase``), which never sees a catalogue reference.

- ``fixed_section``: the revise message's statement of the instructor's locks and overrides as
  fixed, each block by segment and interval index and class time, the overridden values given,
  and of the base mix's pinned cuts by track index (``revise_pins.pin_lines``). Anchors name
  recordings, so they are not stated.
- ``reconciler``: what ``submit_draft`` runs on the model's checked draft. First the overlay's cut
  pins are re-applied to the model's new spec (``revise_pins.pinned_mix``, #1423: by target, by
  Apply's rules, a spec that cannot hold them a correction round) and the model's fitted workout
  moved onto the pinned mix. Then the overlay's other records are re-applied to that workout by
  ``edit_overlay_policy.reapplied``'s fixed order (locks, boundary overrides, attribute
  overrides, anchors) against the base's workout; a lock whose block the model changed within its
  scope is ``ToolError("overlay_violated")``, a correction round, naming the block. The re-applied
  workout is then checked again on the (pinned) composed map (``agent.design.check``, the plan
  gates; its refusal is a correction round too), and a timing-locked block that fit moved is
  ``overlay_violated``. The result (``agent.studio_design.Reconciled``) is the checked draft, the
  mix it was checked on, the reconciled overlay (the records retargeted on the fitted workout, the
  unapplied and orphaned ones kept, every pin record carried) and the pinned spec to store
  (``None`` when no pin applies, so the model's spec is stored as before). A reconciled overlay
  past its bound is ``ToolError("overlay_too_large")``, a correction round.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mowl.workout import mmss
from pydantic import ValidationError

from classbeat.agent.design import DesignResult, check, within_plan
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.agent.studio_design import Reconciled
from classbeat.agent.studio_mix import Composed
from classbeat.design.edit_overlay_model import EditOverlay, Lock, OverlayPin, Override, Record
from classbeat.design.edit_overlay_policy import (
    OVERLAY_VIOLATED,
    OverlayViolated,
    event_ms,
    match,
    reapplied,
    retargeted,
)
from classbeat.design.workout_edit_policy import Block, EditRefused, blocks
from classbeat.plan_gates import PlanGates
from classbeat.swedebeat_studio.mix_apply_calc import StudioTracks
from classbeat.swedebeat_studio.revise_pins import pin_lines, pinned_mix
from classbeat.swedebeat_studio.studio_edit_calc import fitted_content, overlay_of

_TOO_LARGE = "the instructor's edits no longer fit this draft; keep its blocks closer to base_draft"
Reconcile = Callable[[DesignResult, Composed], Reconciled]


def _where(block: Block) -> str:
    return (
        f"segment {block.segment} interval {block.index} ({block.kind}, "
        f"{mmss(block.start_ms // 1000)}-{mmss(block.end_ms // 1000)})"
    )


def fixed_section(
    base: dict[str, Any],
    overlay: EditOverlay,
    base_spec: Mapping[str, Any] | None = None,
    base_plan: Mapping[str, Any] | None = None,
) -> str:
    """The revise message's section of fixed edits (module docstring); ``""`` with none.
    ``base_spec`` and ``base_plan``: the base mix by reference and by track index."""
    found = blocks(base)
    lines: list[str] = []
    for record in overlay.records:
        if not isinstance(record, Lock | Override):
            continue
        at = match(record.target, found)
        if at is None:
            continue  # unapplied: without effect, so not stated
        where = _where(found[at])
        if isinstance(record, Lock):
            lines.append(f"- {where}: locked ({record.scope}); keep it exactly as in base_draft")
        elif record.field == "boundary":
            lines.append(f"- {where}: the instructor set its end; keep its end time")
        else:
            lines.append(f"- {where}: the instructor set {record.field} = {record.value!r}")
    if base_spec is not None and base_plan is not None and overlay.pins():
        lines += pin_lines(base_spec, base_plan)
    if not lines:
        return ""
    head = "The instructor's own edits are fixed. Keep each of them in the revised draft:\n"
    return head + "\n".join(lines) + "\n\n"


def _violated(record: Record, found: Sequence[Block]) -> ToolError:
    at = match(record.target, found) if isinstance(record, Lock | Override) else None
    where = "a locked block" if at is None else _where(found[at])
    return ToolError(OVERLAY_VIOLATED, [f"{where} is locked by the instructor; keep it as it was"])


def reconciler(
    base: dict[str, Any],
    overlay: EditOverlay,
    music: StudioTracks,
    target_sec: int,
    analysed: Mapping[str, str | None],
    gates: PlanGates | None,
) -> Reconcile:
    """The function the revise stage runs on each submitted draft (module docstring)."""
    records = [r for r in overlay.records if not isinstance(r, OverlayPin)]
    pins = list(overlay.pins())
    before = blocks(base)
    references = music.references

    def reconcile(result: DesignResult, composed: Composed) -> Reconciled:
        content = fitted_content(result.draft.model_dump(mode="json"), result.spec)
        model = {
            "tracks": [
                {"reference": references[t["track"]], **{k: t[k] for k in ("start_sec", "end_sec")}}
                for t in composed.plan["tracks"]
            ],
            "crossfades": list(composed.plan["crossfades"]),
        }
        mix = pinned_mix(model, composed, content, music, target_sec, pins)
        spec = model if mix.spec is None else mix.spec
        try:
            applied = reapplied(mix.content, base, records, event_ms=event_ms(spec, analysed))
        except OverlayViolated as violated:
            raise _violated(violated.record, before) from None
        try:
            draft = WorkoutDraft.model_validate(applied.content)
        except ValidationError:
            raise ToolError(
                "invalid_draft", ["the instructor's edits do not fit this draft"]
            ) from None
        again = within_plan(gates, check(draft, mix.composed.map))
        fitted = fitted_content(applied.content, again.spec)
        was, now = blocks(applied.content), blocks(fitted)
        for record, at in applied.placed:
            moved = (was[at].start_ms, was[at].end_ms) != (now[at].start_ms, now[at].end_ms)
            if isinstance(record, Lock) and record.covers_timing() and moved:
                raise _violated(record, before)
        kept: list[Record] = [*applied.unapplied, *applied.orphaned]
        try:
            made = overlay_of([*retargeted(applied.placed, fitted), *kept, *pins])
        except EditRefused:  # past the bound: the model's draft, not the job's end
            raise ToolError("overlay_too_large", [_TOO_LARGE]) from None
        return Reconciled(again, mix.composed, made, mix.spec)

    return reconcile
