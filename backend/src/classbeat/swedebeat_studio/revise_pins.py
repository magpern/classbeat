"""The edit overlay's cut pins through a studio revise (ADR 0042 section 5, "Pins and regeneration",
#1423). No I/O: ``revise_overlay`` runs these on each submitted draft, ``revise_kind`` states the
pins in the revise message.

- ``pinned_mix``: the model's checked mix with the overlay's pin records applied
  (``studio.mix_revise_pin_policy.revised``: by target, under the choices B9 implies for the mix,
  then ``mix_plan_policy.checked`` and the class length, the rules **Apply these choices** runs),
  composed again, and the model's fitted workout moved onto it by B4a's song-anchored remap
  (``mix_remap_calc``), as Apply moves a workout. A pinned spec the gates refuse is
  ``ToolError("invalid_mix")`` naming the reason and the pinned cuts, a correction round (so is a
  pinned spec B9's ``holds`` would not accept, ``held``, the final gate): the model may change any
  other cut or crossfade, never a pin (a confirmed one stays exactly at its ``confirmed_sec``). If
  no correction holds them, the run ends ``invalid_output`` and nothing is stored. A remap the
  workout cannot follow is ``ToolError("invalid_draft")``, a correction round too. With no pin that
  applies, the model's mix and workout stand unchanged (``spec`` is ``None``).
- ``pin_lines``: the revise message's statement of the base mix's pinned cuts, by the list's track
  index and the cut's second (never a catalogue reference), so the model keeps them.
"""

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from classbeat.agent.stage import ToolError
from classbeat.agent.studio_mix import Composed, compose_spec
from classbeat.studio import mix_remap_calc
from classbeat.studio.mix_choice_model import MixChoiceRefused
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.studio.mix_pin_policy import pins_of
from classbeat.studio.mix_revise_pin_policy import held, revised
from classbeat.swedebeat_studio.mix_apply_calc import StudioTracks, keyed, plan_of

_KEEP = (
    "the instructor pinned {cuts}; a pinned cut stays where the instructor put it, so change "
    "the other cuts or the crossfades instead"
)


class PinnedMix(NamedTuple):
    spec: dict[str, Any] | None  # the pinned spec by reference; None: no pin applies
    composed: Composed  # the mix the workout is checked on
    content: dict[str, Any]  # the model's workout, on that mix


def _cuts(spec: Mapping[str, Any], references: Sequence[str]) -> str:
    index = {r: n for n, r in enumerate(references)}
    named = [
        f"track {index.get(str(spec['tracks'][n]['reference']), '?')}'s {edge} cut "
        f"at {pin.proposed():.2f} s" + (" (confirmed)" if pin.confirmed else "")
        for n, edge, pin in pins_of(spec)
    ]
    return ", ".join(named) or "a cut"


def _requested(pins: Sequence[PinRecord], references: Sequence[str]) -> str:
    index = {r: n for n, r in enumerate(references)}
    return ", ".join(
        f"track {index.get(p.reference, '?')}'s {p.edge} cut at {p.proposed():.2f} s"
        + (" (confirmed)" if p.confirmed else "")
        for p in pins
    )


def pinned_mix(
    model: dict[str, Any],
    composed: Composed,
    content: dict[str, Any],
    music: StudioTracks,
    target_sec: int,
    pins: Sequence[PinRecord],
) -> PinnedMix:
    """The model's mix ``model`` (by reference, checked) with ``pins`` (module docstring)."""
    references = music.references
    try:
        found = revised(model, references, music.facts(), target_sec, pins)
    except MixChoiceRefused as refused:
        keep = _KEEP.format(cuts=_requested(pins, references))
        raise ToolError("invalid_mix", [f"{refused.reason}: {refused}", keep]) from None
    if not pins_of(found.spec):
        return PinnedMix(None, composed, content)
    try:
        held(found.spec, references, music.facts(), target_sec, pins)
    except MixChoiceRefused as refused:  # B9 would answer ``mix_not_applied``: a correction round
        keep = _KEEP.format(cuts=_requested(pins, references))
        raise ToolError("invalid_mix", [f"{refused.reason}: {refused}", keep]) from None
    spec = found.spec
    keep = _KEEP.format(cuts=_cuts(spec, references))
    try:
        again = compose_spec(music.tracks, keyed(references, spec))
    except ToolError as refused:
        raise ToolError("invalid_mix", [*refused.messages, keep]) from None
    if again.plan != plan_of(references, spec):  # ``revised`` returned a checked spec
        raise ToolError("invalid_mix", [keep])
    flat = [i for s in content["segments"] for i in s["intervals"]]
    try:
        lengths = mix_remap_calc.remapped(model, spec, [int(i["sec"]) for i in flat])
    except (MixChoiceRefused, ValueError):
        refused = f"the workout does not fit the pinned mix; {keep}"
        raise ToolError("invalid_draft", [refused]) from None
    moved = {**content, "segments": [dict(s) for s in content["segments"]]}
    at = iter(lengths)
    for segment in moved["segments"]:
        segment["intervals"] = [{**i, "sec": next(at)} for i in segment["intervals"]]
    return PinnedMix(spec, again, moved)


def pin_lines(base_spec: Mapping[str, Any], base_plan: Mapping[str, Any]) -> list[str]:
    """The base mix's pinned cuts as the revise message states them (module docstring)."""
    lines: list[str] = []
    for n, edge, pin in pins_of(base_spec):
        track = base_plan["tracks"][n]
        how = "confirmed after listening" if pin.confirmed else "set"
        lines.append(
            f"- track {track['track']}: its {edge} cut is pinned by the instructor ({how}) at "
            f"{float(track[f'{edge}_sec']):.2f} s; keep it"
        )
    return lines
