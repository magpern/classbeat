"""The user's mix choices applied to the model's proposal (ADR 0036 amendment B, B4 to B7, #1367).
Pure: no I/O. What the screen shows per song is ``mix_suggestion_policy.suggestions`` (B3, B5).

- ``applied``: the derived spec by the fixed rules of B4, always from the **proposal**:
  1. a song is **whole** when ``dj_mix`` or ``trim`` is off, its position is in ``keep_whole`` or
     its stored timing is not ``exact`` (B5); a whole song is one piece ``[0, length]`` (the
     analysed length) at the place of its **first** piece, its other pieces dropped (B7);
  2. each change of piece keeps the proposal's crossfade only when the same two pieces were
     adjacent in the proposal (a join made by dropping pieces is 0); ``blend`` off sets every
     crossfade 0, and a song in ``hard_cut_in`` (``hard_cut_out``) gets 0 on the change into (out
     of) each of its pieces;
  3. ``mix_plan_policy.checked``, unchanged; its refusal is raised by name, never repaired;
  4. the class length (B6): the composed duration within ``length_policy.within`` of the list's
     ``target_sec``, else ``class_length_out_of_tolerance`` with the total and the allowed range.
  ``invalid_input`` (a position off the list) comes before all of them (B4, the one order).
- ``pinned``: ``applied``, then the edit overlay's cut pins written into it by target
  (``mix_pin_policy.with_pins``; ADR 0042 sections 5 and 8, #1390; a song made whole has none, its
  pins unapplied), run through steps 3 and 4 again, so a pinned spec is held to the same gates.
  Without a pin that applies it is ``applied``'s spec, unchanged.
- ``is_plain``: every piece ``[0, length]`` in whole hundredths, every crossfade 0 and no reference
  twice (B9's ``mixed`` is its negation).
"""

import itertools
from collections.abc import Mapping, Sequence
from typing import Any, Final

from classbeat.agent.songs import TOLERANCE
from classbeat.studio import mix_plan_policy
from classbeat.studio.mix_choice_model import (
    MixChoiceRefused,
    MixChoices,
    Proposal,
    approved_facts,
    pieces,
)
from classbeat.studio.mix_choice_model import positions_refusal as _positions
from classbeat.studio.mix_facts_model import CS, TrackFacts, cs
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.studio.mix_pin_policy import Pinned, pins_of, with_pins
from classbeat.swedebeat_studio import length_policy

OUT_OF_TOLERANCE: Final = "class_length_out_of_tolerance"


def allowed(target_sec: int) -> tuple[float, float]:
    """The class bounds of ``length_policy.within`` (B6), for the refusal's detail."""
    return target_sec * (1 - TOLERANCE), target_sec * (1 + length_policy.UPPER)


def whole_songs(
    proposal: Proposal, known: Mapping[str, TrackFacts], choices: MixChoices
) -> frozenset[str]:
    """The approved references B4 step 1 plays whole (module docstring)."""
    at = {r: n for n, r in enumerate(proposal.approved) if r is not None}
    return frozenset(
        r
        for r in known
        if not choices.dj_mix
        or not choices.trim
        or at[r] in choices.keep_whole
        or known[r].timing_class != "exact"
    )


def applied(
    proposal: Proposal, facts: Mapping[str, TrackFacts], choices: MixChoices
) -> dict[str, Any]:
    """The derived, checked spec (module docstring), else ``MixChoiceRefused`` by name."""
    _positions(choices, len(proposal.approved))
    known = dict(approved_facts(proposal, facts))
    made_whole = whole_songs(proposal, known, choices)

    def whole(reference: str) -> bool:
        return reference in made_whole

    kept: list[tuple[int, str, float, float]] = []  # (proposal position, reference, start, end)
    seen: set[str] = set()
    for n, (reference, start, end) in enumerate(pieces(proposal.spec)):
        if reference not in known or not whole(reference):
            kept.append((n, reference, start, end))  # an unknown reference: ``checked`` refuses
        elif reference not in seen:
            kept.append((n, reference, 0.0, cs(known[reference].length) / CS))
        seen.add(reference)
    fades = list(proposal.spec["crossfades"])
    if len(fades) != len(pieces(proposal.spec)) - 1:
        raise MixChoiceRefused("crossfade_count", "one crossfade per change of piece")
    cut_in = {proposal.approved[p] for p in choices.hard_cut_in}
    cut_out = {proposal.approved[p] for p in choices.hard_cut_out}
    crossfades: list[object] = []
    for (m, left, _, _), (n, right, _, _) in itertools.pairwise(kept):
        hard = not choices.blend or left in cut_out or right in cut_in
        crossfades.append(0 if hard or n != m + 1 else fades[m])
    spec = {
        "tracks": [{"reference": r, "start_sec": a, "end_sec": b} for _, r, a, b in kept],
        "crossfades": crossfades,
    }
    return _gated(spec, proposal, known)


def pinned(
    proposal: Proposal,
    facts: Mapping[str, TrackFacts],
    choices: MixChoices,
    pins: Sequence[PinRecord],
) -> Pinned:
    """``applied`` with the overlay's pins, gated again (module docstring); ``MixChoiceRefused``
    by name, ``ValueError`` for records that do not read."""
    base = applied(proposal, facts, choices)
    if not pins:
        return Pinned(base)
    known = dict(approved_facts(proposal, facts))
    found = with_pins(
        base, pins, approved=proposal.approved, whole=whole_songs(proposal, known, choices)
    )
    if not pins_of(found.spec):
        return Pinned(base, found.unapplied, found.dropped)
    return Pinned(_gated(found.spec, proposal, known), found.unapplied, found.dropped)


def _gated(
    spec: Mapping[str, Any], proposal: Proposal, known: Mapping[str, TrackFacts]
) -> dict[str, Any]:
    """Steps 3 and 4 of the module docstring."""
    try:
        good = mix_plan_policy.checked(spec, proposal.approved, known)
    except mix_plan_policy.MixPlanRefused as exc:
        raise MixChoiceRefused(exc.reason, str(exc)) from exc
    total = composed_sec(good)
    if not length_policy.within(total, proposal.target_sec):
        low, high = allowed(proposal.target_sec)
        raise MixChoiceRefused(
            OUT_OF_TOLERANCE,
            f"the mix is {total:.2f} s; the class allows {low:.0f} to {high:.0f} s",
            total,
            (low, high),
        )
    return good


def composed_sec(spec: Mapping[str, Any]) -> float:
    """The one duration rule on a spec: its windows less its crossfades (section 4.3)."""
    played = sum(cs(b) - cs(a) for _, a, b in pieces(spec))
    return (played - CS * sum(int(c) for c in spec["crossfades"])) / CS


def is_plain(spec: Mapping[str, Any], lengths: Mapping[str, float]) -> bool:
    """Whole songs: each piece ``[0, length]`` in whole hundredths, crossfades 0, no repeat."""
    found = pieces(spec)
    references = [r for r, _, _ in found]
    return (
        len(set(references)) == len(references)
        and all(r in lengths and cs(a) == 0 and cs(b) == cs(lengths[r]) for r, a, b in found)
        and all(int(c) == 0 for c in spec["crossfades"])
    )
