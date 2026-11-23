"""The edit overlay's cut pins re-applied to a studio revise's new model spec (ADR 0042 section 5,
"Pins and regeneration", #1423). Pure: no I/O.

A revise draft is itself a proposal (``mix_proposal_policy``), so B9 holds it to
``mix_choice_policy.pinned(proposal, facts, implied, overlay pins)`` with the proposal's spec its
own. ``revised`` makes that spec from the model's: the model's checked spec, never
``applied(...)``'s, with the overlay's pin records written in by target
(``mix_pin_policy.with_pins``) under the choices B9 implies for the mix (``MixChoices.dj_defaults``:
a song whose stored timing is not ``exact`` is whole, its pins unapplied), then the same gates as
**Apply these choices** (``mix_choice_policy.gated``: ``mix_plan_policy.checked``, then the class
length, B6). So each pin lands by its rule (``bar`` snapped, ``exact`` as requested, a confirmed one
exactly at ``confirmed_sec``, never moved), and a spec that cannot hold a pin is
``MixChoiceRefused`` by name, never repaired: a confirmed pin is never moved to make it fit.

A record that cannot apply (its piece gone, its song whole, its song off the list) is reported,
kept and without effect. With no record that applies the model's spec is returned exactly as it
is, so a revise of a draft without pins stores what it stored before.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from classbeat.studio.mix_applied_policy import holds
from classbeat.studio.mix_choice_model import (
    MixChoiceRefused,
    MixChoices,
    Proposal,
    approved_facts,
)
from classbeat.studio.mix_choice_policy import gated, whole_songs
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.studio.mix_pin_policy import Pinned, pins_of, with_pins
from classbeat.studio.mix_spec_model import MixSpec


def revised(
    spec: Mapping[str, Any],
    approved: Sequence[str],
    facts: Mapping[str, TrackFacts],
    target_sec: int,
    pins: Sequence[PinRecord],
) -> Pinned:
    """The model's checked ``spec`` with ``pins`` (module docstring); ``MixChoiceRefused`` by
    name for a pinned spec the gates refuse, ``ValueError`` for records that do not read."""
    model = {"tracks": [dict(t) for t in spec["tracks"]], "crossfades": list(spec["crossfades"])}
    if not pins:
        return Pinned(model)
    proposal = Proposal(approved, model, target_sec)
    known = dict(approved_facts(proposal, facts))
    whole = whole_songs(proposal, known, MixChoices.dj_defaults())
    found = with_pins(model, pins, approved=approved, whole=whole)
    if not pins_of(found.spec):
        return Pinned(model, found.unapplied, found.dropped)
    return Pinned(gated(found.spec, proposal, known), found.unapplied, found.dropped)


def held(
    spec: Mapping[str, Any],
    approved: Sequence[str],
    facts: Mapping[str, TrackFacts],
    target_sec: int,
    pins: Sequence[PinRecord],
) -> None:
    """The final gate: the pinned ``spec``, as the revise draft (a proposal, its spec its own), must
    pass B9's ``holds`` under the DJ-mix choices with ``pins``, or the outputs answer
    ``mix_not_applied`` for it. ``MixChoiceRefused("pin_not_held")`` otherwise, so the model gets a
    correction round instead of a stored draft that fails closed. Before ``revised``'s gates
    a spec with no pin is exempt: nothing to hold."""
    if not pins_of(spec):
        return
    try:
        stored = MixSpec.model_validate(spec).model_dump(mode="json")  # the stored form
    except ValueError:
        stored = {}
    proposal = Proposal(approved, stored, target_sec)
    if not holds(stored, proposal, facts, MixChoices.dj_defaults(), pins):
        raise MixChoiceRefused(
            "pin_not_held", "the mix does not hold the pins when the outputs re-derive it"
        )
