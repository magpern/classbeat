"""#1423 (ADR 0042 section 5, "Pins and regeneration"): the overlay's cut pins re-applied to a
studio revise's new model spec (``studio.mix_revise_pin_policy.revised``). Each pin lands by its
rule (requested, effective, confirmed), a confirmed pin never moves, the pinned spec passes B9's
``holds`` as the revise draft (a proposal) is checked, a spec that cannot hold a pin is refused by
name, an unconfirmed pin's changed file is ``pin_unconfirmed`` at the outputs, and with no pin
that applies the model's spec is returned unchanged. Pure: no I/O."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from classbeat.design.edit_overlay_model import OverlayPin, of_records
from classbeat.studio.mix_applied_policy import holds
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal
from classbeat.studio.mix_choice_policy import composed_sec
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.studio.mix_revise_pin_policy import revised
from classbeat.studio.mix_spec_model import MixSpec
from classbeat.studio.output_choice_policy import refusal
from classbeat.swedebeat_studio.applied_form import unconfirmed
from mix_choice_fixtures import FACTS, ORDER, TARGET, spec, track

DJ = MixChoices.dj_defaults()
SHA = "a" * 64
OTHER = "b" * 64
# The model's new spec: b's end moved from 220 to 230 and c and d swapped (a regeneration). 806 s.
MODEL = spec(
    ("a", 0.0, 180.0), ("b", 20.0, 230.0), ("d", 40.0, 240.0), ("c", 0.0, 240.0),
    crossfades=[8, 6, 10],
)  # fmt: skip


def record(
    reference: str,
    edge: str,
    requested: float,
    *,
    mode: str = "bar",
    confirmed: float | None = None,
    against: str = SHA,
) -> OverlayPin:
    extra: dict[str, Any] = {"set_against": SHA}
    if confirmed is not None:
        extra |= {"confirmed_sec": confirmed, "confirmed_against": against}
    return OverlayPin.model_validate(
        {"type": "pin", "reference": reference, "occurrence": 0, "edge": edge,
         "requested_sec": requested, "mode": mode, "confirmed": confirmed is not None, **extra}
    )  # fmt: skip


def stored(pins: tuple[PinRecord, ...], model: dict[str, Any] = MODEL) -> dict[str, Any]:
    made = revised(model, ORDER, FACTS, TARGET, pins)
    return MixSpec.model_validate(made.spec).model_dump(mode="json")


def b9_holds(value: dict[str, Any], pins: tuple[PinRecord, ...]) -> bool:
    """B9 on the revise draft: a proposal, so its own spec is the proposal's (``basis``)."""
    return holds(value, Proposal(ORDER, value, TARGET), FACTS, DJ, pins)


def end_of(value: dict[str, Any], reference: str) -> float:
    return next(t["end_sec"] for t in value["tracks"] if t["reference"] == reference)


def test_without_a_pin_the_models_spec_is_returned_as_it_is() -> None:
    assert revised(MODEL, ORDER, FACTS, TARGET, ()).spec == MODEL
    whole = {**FACTS, "b": track("approximate")}  # b has no cut under DJ mix: its pin unapplied
    pin = record("b", "end", 200.0, mode="exact")
    made = revised(MODEL, ORDER, whole, TARGET, (pin,))
    assert made.spec == MODEL
    assert made.unapplied == (pin,)


@pytest.mark.parametrize(
    ("pin", "effective"),
    [
        (record("b", "end", 218.7), 218.0),  # requested, bar: snapped (2 s bars from 0)
        (record("b", "end", 217.37, mode="exact"), 217.37),  # requested, exact: kept
        (record("b", "end", 218.7, confirmed=219.37), 219.37),  # confirmed: exactly that
    ],
    ids=["bar", "exact", "confirmed"],
)
def test_a_pin_survives_the_revise_by_its_rule_and_passes_b9(
    pin: PinRecord, effective: float
) -> None:
    value = stored((pin,))
    assert end_of(value, "b") == effective  # the model's 230 s cut does not stand
    track_b = next(t for t in value["tracks"] if t["reference"] == "b")
    assert track_b["end_pin"] == pin.pin().stored()
    assert [t["reference"] for t in value["tracks"]] == ["a", "b", "d", "c"]  # the model's order
    assert b9_holds(value, (pin,))
    assert not b9_holds(value, ())  # a stored pin without its record: fail closed
    assert not b9_holds(MixSpec.model_validate(MODEL).model_dump(mode="json"), (pin,))


def test_a_confirmed_pin_never_moves_whatever_the_model_cut() -> None:
    pin = record("a", "end", 179.0, mode="exact", confirmed=179.0)  # beside the 180 s phrase point
    for model_end in (180.0, 170.0, 200.0):
        model = json.loads(json.dumps(MODEL))
        model["tracks"][0]["end_sec"] = model_end
        value = stored((pin,), model)
        assert end_of(value, "a") == 179.0
        assert b9_holds(value, (pin,))


def test_a_revise_that_cannot_hold_a_confirmed_pin_is_refused_by_name() -> None:
    """MODEL is 806 s, the class allows 792 to 968 s: b's confirmed cut at 190 s makes 766 s."""
    pin = record("b", "end", 190.0, mode="exact", confirmed=190.0)
    with pytest.raises(MixChoiceRefused) as caught:
        revised(MODEL, ORDER, FACTS, TARGET, (pin,))
    assert caught.value.reason == "class_length_out_of_tolerance"
    assert caught.value.total_sec == 766.0


def test_the_hard_rules_still_refuse_a_pinned_spec() -> None:
    with pytest.raises(MixChoiceRefused) as caught:
        revised(MODEL, ORDER, FACTS, TARGET, (record("b", "end", 70.0, mode="exact"),))
    assert caught.value.reason == "window_too_short"


def test_a_pinned_revise_keeps_the_class_length() -> None:
    value = stored((record("b", "end", 218.7),))
    assert composed_sec(value) == 794.0


def overlay_draft(value: dict[str, Any], pins: tuple[PinRecord, ...], sha: str) -> Any:
    analyses = [SimpleNamespace(reference=r, sha256=sha) for r in ORDER]
    return SimpleNamespace(
        spec=value, overlay=of_records(list(pins)), music=SimpleNamespace(analyses=analyses)
    )


@pytest.mark.parametrize(
    "pin",
    [record("b", "end", 218.7), record("b", "end", 218.7, confirmed=219.37)],
    ids=["unconfirmed", "confirmed"],
)
def test_a_pin_whose_file_changed_is_pin_unconfirmed_at_the_outputs(pin: PinRecord) -> None:
    value = stored((pin,))
    assert not unconfirmed(overlay_draft(value, (pin,), SHA))  # the file it was set against
    found = overlay_draft(value, (pin,), OTHER)  # a new analysed file
    assert unconfirmed(found)
    chosen = ("swedebeat_playlist", "swedebeat_mix")
    gate = {"repeats": False, "mixed": True, "applied_form": b9_holds(value, (pin,))}
    assert refusal(chosen, chosen, **gate, pin_unconfirmed=unconfirmed(found)) == (
        "pin_unconfirmed"
    )
    assert refusal(chosen, chosen, **gate, pin_unconfirmed=False) is None
