"""#1390 (ADR 0042 sections 5 and 8): the overlay's cut pins after ``mix_choice_policy.applied``
(``pinned``, ``mix_pin_policy.with_pins``) and in B9's ``holds``: by target, repeats by
``occurrence``, a whole song's pins unapplied, a replaced song's dropped, the gates run again on the
pinned spec, and no pin leaves ``applied``'s spec byte-identical. Pure: no I/O."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.design.revision import studio_hash
from classbeat.studio.mix_applied_policy import holds
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal, parsed
from classbeat.studio.mix_choice_policy import applied, composed_sec, pinned
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.studio.mix_pin_policy import occurrences, records, with_pins
from classbeat.studio.mix_spec_model import MixSpec
from mix_choice_fixtures import FACTS, ORDER, PLAIN, REPEAT, proposal, spec, track

DJ = MixChoices.dj_defaults()
WHOLE = MixChoices.whole_songs()
SHA = "a" * 64


def choose(**changes: Any) -> MixChoices:
    return parsed({"dj_mix": True, "blend": True, "trim": True, **changes})


def record(
    reference: str,
    edge: str,
    requested: float,
    *,
    occurrence: int = 0,
    mode: str = "bar",
    confirmed: float | None = None,
) -> PinRecord:
    extra: dict[str, Any] = {}
    if confirmed is not None:
        extra = {"confirmed_sec": confirmed, "confirmed_against": SHA}
    return PinRecord.model_validate(
        {"reference": reference, "occurrence": occurrence, "edge": edge,
         "requested_sec": requested, "mode": mode, "confirmed": confirmed is not None, **extra}
    )  # fmt: skip


def cuts(value: dict[str, Any]) -> list[tuple[str, float, float]]:
    return [(t["reference"], t["start_sec"], t["end_sec"]) for t in value["tracks"]]


def test_without_a_pin_the_spec_is_applieds_byte_for_byte() -> None:
    base = applied(proposal(PLAIN), FACTS, DJ)
    for pins in ((), (record("z", "end", 100.0),), (record("a", "end", 100.0, occurrence=3),)):
        made = pinned(proposal(PLAIN), FACTS, DJ, pins).spec
        assert json.dumps(made, sort_keys=True) == json.dumps(base, sort_keys=True)
        assert studio_hash({}, MixSpec.model_validate(made).model_dump(mode="json")) == (
            studio_hash({}, base)
        )


def test_a_pin_is_written_by_target_and_checked() -> None:
    made = pinned(proposal(PLAIN), FACTS, DJ, (record("b", "end", 218.7),))
    assert cuts(made.spec)[1] == ("b", 20.0, 218.0)  # snapped(218.7), 2 s bars from 0
    assert made.spec["tracks"][1]["end_pin"] == {
        "requested_sec": 218.7, "mode": "bar", "confirmed": False
    }  # fmt: skip
    assert "end_pin" not in made.spec["tracks"][0]
    assert made.unapplied == ()
    assert made.dropped == ()
    assert composed_sec(made.spec) == 794.0
    MixSpec.model_validate(made.spec)


def test_the_class_length_rule_holds_on_the_pinned_spec() -> None:
    """796 s unpinned, 880 s target (792 to 968 s): a pin that shortens b by 20 s is refused."""
    with pytest.raises(MixChoiceRefused) as caught:
        pinned(proposal(PLAIN), FACTS, DJ, (record("b", "end", 200.0, mode="exact"),))
    assert caught.value.reason == "class_length_out_of_tolerance"
    assert caught.value.total_sec == 776.0


@pytest.mark.parametrize(
    ("pins", "reason"),
    [
        ((record("b", "end", 241.0, mode="exact"),), "cut_out_of_range"),
        ((record("b", "end", 70.0, mode="exact"),), "window_too_short"),
        ((record("b", "start", 30.0, mode="exact"), record("b", "end", 20.0, mode="exact")),
         "cut_out_of_range"),
    ],
)  # fmt: skip
def test_checkeds_refusals_reach_the_pinned_spec(pins: tuple[PinRecord, ...], reason: str) -> None:
    with pytest.raises(MixChoiceRefused) as caught:
        pinned(proposal(PLAIN), FACTS, DJ, pins)
    assert caught.value.reason == reason


def test_invalid_input_comes_before_any_pin() -> None:
    with pytest.raises(MixChoiceRefused) as caught:
        pinned(proposal(PLAIN), FACTS, choose(keep_whole=[9]), (record("b", "end", 241.0),))
    assert caught.value.reason == "invalid_input"


@pytest.mark.parametrize(
    "choices",
    [WHOLE, choose(trim=False), choose(keep_whole=[1])],
    ids=["whole_songs", "trim_off", "keep_whole"],
)
def test_a_pin_on_a_song_made_whole_is_unapplied(choices: MixChoices) -> None:
    pins = (record("b", "end", 200.0, mode="exact"),)
    made = pinned(proposal(PLAIN), FACTS, choices, pins)
    assert made.spec == applied(proposal(PLAIN), FACTS, choices)
    assert made.unapplied == pins
    assert "end_pin" not in made.spec["tracks"][1]


def test_a_pin_on_a_track_without_exact_timing_is_unapplied() -> None:
    known = {**FACTS, "b": track("approximate")}
    made = pinned(proposal(PLAIN), known, DJ, (record("b", "end", 218.0, mode="exact"),))
    assert made.unapplied
    assert not any("end_pin" in t for t in made.spec["tracks"])


def test_repeats_are_pinned_by_occurrence() -> None:
    """REPEAT plays a at pieces 0 and 2: occurrence 1 is piece 2; occurrence 2 does not exist."""
    pins = (
        record("a", "start", 121.0, occurrence=1, mode="exact"),
        record("a", "end", 99.0, occurrence=2, mode="exact"),
    )
    made = pinned(proposal(REPEAT, 840), FACTS, DJ, pins)
    assert occurrences(made.spec)[2] == ("a", 1)
    assert made.spec["tracks"][2]["start_sec"] == 121.0
    assert "start_pin" in made.spec["tracks"][2]
    assert "start_pin" not in made.spec["tracks"][0]
    assert made.unapplied == pins[1:]


def test_a_replaced_song_drops_its_pins_with_a_notice() -> None:
    pins = (record("z", "end", 100.0), record("b", "end", 218.7))
    made = pinned(proposal(PLAIN), FACTS, DJ, pins)
    assert made.dropped == pins[:1]
    assert made.spec["tracks"][1]["end_sec"] == 218.0


def test_pins_survive_a_new_model_spec_by_target() -> None:
    """Regeneration: the model's new spec carries no pin; the records go where b now plays."""
    new = spec(
        ("b", 20.0, 220.0), ("a", 0.0, 180.0), ("c", 0.0, 240.0), ("d", 40.0, 240.0),
        crossfades=[8, 6, 10],
    )  # fmt: skip
    pins = (record("b", "end", 218.7), record("a", "start", 0.0, mode="exact"))
    made = pinned(proposal(new), FACTS, DJ, pins)
    assert cuts(made.spec)[0] == ("b", 20.0, 218.0)
    assert made.spec["tracks"][1]["start_pin"]["mode"] == "exact"


def test_with_pins_replaces_any_pin_the_spec_carried() -> None:
    first = pinned(proposal(PLAIN), FACTS, DJ, (record("b", "end", 218.7),)).spec
    again = with_pins(first, (record("d", "end", 238.7),)).spec
    assert "end_pin" not in again["tracks"][1]
    assert again["tracks"][3]["end_pin"]["requested_sec"] == 238.7


def test_a_target_is_pinned_once() -> None:
    with pytest.raises(ValueError, match="pinned once"):
        records([record("b", "end", 218.0), record("b", "end", 216.0)])
    with pytest.raises(ValueError, match="pinned once"):
        pinned(proposal(PLAIN), FACTS, DJ, (record("b", "end", 218.0), record("b", "end", 1.0)))


@pytest.mark.parametrize(
    "bad",
    [
        {"reference": "b", "occurrence": -1, "edge": "end"},
        {"reference": "b", "occurrence": 0, "edge": "middle"},
        {"reference": "b c", "occurrence": 0, "edge": "end"},
        {"reference": "b", "occurrence": 0, "edge": "end", "position": 1},
    ],
)
def test_a_record_that_does_not_read_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        records([{**bad, "requested_sec": 200.0, "mode": "bar", "confirmed": False}])


# --- B9's holds with the overlay's pins ---------------------------------------------------------


def test_holds_with_the_pins_the_overlay_has_and_only_with_them() -> None:
    pins = (record("b", "end", 218.7),)
    stored = MixSpec.model_validate(pinned(proposal(PLAIN), FACTS, DJ, pins).spec)
    as_stored = stored.model_dump(mode="json")
    assert holds(as_stored, proposal(PLAIN), FACTS, DJ, pins)
    assert not holds(as_stored, proposal(PLAIN), FACTS, DJ)  # a pin with no record: fail closed
    plain = applied(proposal(PLAIN), FACTS, DJ)
    assert not holds(plain, proposal(PLAIN), FACTS, DJ, pins)  # a record the spec lacks
    tampered = json.loads(json.dumps(as_stored))
    tampered["tracks"][1]["end_pin"]["requested_sec"] = 218.6  # snaps to the same 218.0
    assert not holds(tampered, proposal(PLAIN), FACTS, DJ, pins)


def test_holds_fails_closed_for_a_pinned_spec_the_gates_refuse() -> None:
    pins = (record("b", "end", 200.0, mode="exact"),)  # class_length_out_of_tolerance
    forged = applied(proposal(PLAIN), FACTS, DJ)
    forged["tracks"][1] = {
        **forged["tracks"][1],
        "end_sec": 200.0,
        "end_pin": pins[0].pin().stored(),
    }
    assert not holds(forged, proposal(PLAIN), FACTS, DJ, pins)


def test_holds_with_unapplied_pins_is_applieds() -> None:
    pins = (record("b", "end", 200.0, mode="exact"),)
    assert holds(applied(proposal(PLAIN), FACTS, WHOLE), proposal(PLAIN), FACTS, WHOLE, pins)


def test_a_proposal_is_never_a_pinned_draft() -> None:
    """The proposal's own spec, whatever pins it carries, is re-derived without them."""
    pins = (record("b", "end", 218.7),)
    made = pinned(proposal(PLAIN), FACTS, DJ, pins).spec
    again = pinned(Proposal(ORDER, made, 880), FACTS, DJ, ()).spec
    assert not any("end_pin" in t or "start_pin" in t for t in again["tracks"])
