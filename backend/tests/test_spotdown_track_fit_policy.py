"""Suitability refinement (ADR 0040 section 2, plan section 1 step 5;
``classbeat.spotdown.track_fit_policy``): the check per track and the bounded rounds."""

import math
from dataclasses import replace

import pytest

from classbeat.spotdown.track_fit_policy import (
    MAX_ROUNDS,
    Fit,
    SlotNeed,
    TrackFacts,
    check,
    plan_round,
    round_cap,
)

GOOD = TrackFacts("approximate", 128.0, 240.0, True, frozenset({"drop", "build_up"}))
LOW_START = frozenset({"low_energy_start"})
WARM_UP = SlotNeed(bpm_min=110.0, bpm_max=130.0, min_s=180.0, structure=LOW_START)
OK = Fit(True, ())


@pytest.mark.parametrize(
    ("facts", "need", "reasons"),
    [
        (GOOD, WARM_UP, ()),
        (GOOD, SlotNeed(), ()),
        (replace(GOOD, bpm=64.0), WARM_UP, ()),  # half time, read double
        (replace(GOOD, bpm=250.0), WARM_UP, ()),  # double time, read half
        (replace(GOOD, bpm=133.0), WARM_UP, ()),  # within the 3 % tolerance
        (replace(GOOD, bpm=140.0), WARM_UP, ("bpm_unusable",)),
        (replace(GOOD, bpm=None), WARM_UP, ("bpm_unknown",)),
        (replace(GOOD, bpm=math.nan), WARM_UP, ("bpm_unknown",)),
        (replace(GOOD, bpm=None), SlotNeed(), ()),  # the slot needs no BPM
        (replace(GOOD, duration_s=120.0), WARM_UP, ("too_short",)),
        (replace(GOOD, duration_s=None), WARM_UP, ("length_unknown",)),
        (replace(GOOD, duration_s=math.nan), WARM_UP, ("length_unknown",)),
        (GOOD, replace(WARM_UP, max_s=200.0), ("too_long",)),
        (replace(GOOD, low_energy_start=False), WARM_UP, ("structure_missing",)),
        (replace(GOOD, low_energy_start=None), WARM_UP, ("structure_unknown",)),
        (GOOD, SlotNeed(structure=frozenset({"drop", "build_up"})), ()),
        (GOOD, SlotNeed(structure=frozenset({"breakdown"})), ("structure_missing",)),
        (replace(GOOD, timing_class="unusable"), WARM_UP, ("timing_unusable",)),
        (GOOD, replace(WARM_UP, needs_exact=True), ("needs_exact_timing",)),
        (replace(GOOD, timing_class="exact"), replace(WARM_UP, needs_exact=True), ()),
        (
            replace(GOOD, timing_class="unusable", bpm=None, duration_s=60.0),
            WARM_UP,
            ("timing_unusable", "bpm_unknown", "too_short"),
        ),
    ],
)
def test_check(facts: TrackFacts, need: SlotNeed, reasons: tuple[str, ...]) -> None:
    assert check(facts, need) == Fit(not reasons, reasons)


def test_an_unknown_structure_need_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="structure"):
        check(GOOD, SlotNeed(structure=frozenset({"chorus"})))


@pytest.mark.parametrize(("length", "cap"), [(0, 0), (2, 0), (3, 1), (8, 2), (9, 3), (14, 4)])
def test_a_round_replaces_at_most_a_third(length: int, cap: int) -> None:
    assert round_cap(length) == cap


def bad(*reasons: str) -> Fit:
    return Fit(False, reasons)


def test_a_round_replaces_the_worst_first_within_the_cap() -> None:
    fits = [OK, bad("structure_missing"), OK, bad("bpm_unusable"), bad("needs_exact_timing"), OK]
    step = plan_round(fits, rounds_done=0)
    assert step.replace == (4, 3)  # cap 2 of 6, worst first
    assert step.waiting == (1,)
    assert (step.fall_back, step.accept_with_warning, step.done) == ((), (), False)


def test_an_unusable_track_with_mowl_sections_falls_back_at_once() -> None:
    fits = [bad("timing_unusable"), OK, OK, bad("bpm_unusable"), OK, OK]
    step = plan_round(fits, rounds_done=0, has_fallback=frozenset({0}))
    assert step.fall_back == (0,)
    assert step.replace == (3,)


def test_after_the_last_round_the_rest_is_accepted_or_falls_back() -> None:
    fits = [bad("timing_unusable"), bad("bpm_unusable"), OK, bad("too_short"), OK, OK]
    step = plan_round(fits, rounds_done=MAX_ROUNDS)
    assert step.replace == ()
    assert step.waiting == ()
    assert step.fall_back == (0,)
    assert step.accept_with_warning == (1, 3)
    assert step.done


def test_there_are_at_most_two_rounds() -> None:
    fits = [bad("bpm_unusable")] + [OK] * 5
    assert MAX_ROUNDS == 2
    assert plan_round(fits, 0).replace == (0,)
    assert plan_round(fits, 1).replace == (0,)
    assert plan_round(fits, 2).replace == ()
    assert plan_round(fits, 3).accept_with_warning == (0,)


def test_a_short_list_gets_no_round() -> None:
    step = plan_round([bad("bpm_unusable"), OK], rounds_done=0)
    assert (step.replace, step.accept_with_warning, step.done) == ((), (0,), True)


def test_a_list_that_fits_is_done() -> None:
    step = plan_round([OK, OK, OK], rounds_done=0)
    assert step.done
    assert step.replace == step.fall_back == step.accept_with_warning == ()
