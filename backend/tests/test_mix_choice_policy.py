"""ADR 0036 amendment B4 to B7 (#1367): the user's mix choices applied to the proposal
(``mix_choice_policy.applied``), the per-song suggestions and ``is_plain``."""

import json
import math
import random
from typing import Any

import pytest

from classbeat.studio import mix_plan_policy
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal, parsed
from classbeat.studio.mix_choice_policy import allowed, applied, composed_sec, is_plain
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.studio.mix_suggestion_policy import suggestions
from classbeat.swedebeat_studio import length_policy
from mix_choice_fixtures import FACTS, LOOP, ORDER, PLAIN, REPEAT, proposal, spec, track

WHOLE = MixChoices.whole_songs()
DJ = MixChoices.dj_defaults()
LENGTHS = {r: f.length for r, f in FACTS.items()}


def choose(**changes: Any) -> MixChoices:
    return parsed({"dj_mix": True, "blend": True, "trim": True, **changes})


def windows(value: dict[str, Any]) -> list[tuple[str, float, float]]:
    return [(t["reference"], t["start_sec"], t["end_sec"]) for t in value["tracks"]]


def refused(value: Proposal, choices: MixChoices, facts: Any = FACTS) -> MixChoiceRefused:
    with pytest.raises(MixChoiceRefused) as caught:
        applied(value, facts, choices)
    return caught.value


CUT = [("a", 0.0, 180.0), ("b", 20.0, 220.0), ("c", 0.0, 240.0), ("d", 40.0, 240.0)]
FULL = [(r, 0.0, 240.0) for r in ORDER]


@pytest.mark.parametrize(
    ("choices", "tracks", "crossfades"),
    [
        (WHOLE, FULL, [0, 0, 0]),
        (DJ, CUT, [8, 6, 10]),  # the proposal's own choices give it back
        (choose(blend=False), CUT, [0, 0, 0]),
        (choose(trim=False), FULL, [8, 6, 10]),
        (choose(blend=False, trim=False), FULL, [0, 0, 0]),
        (choose(keep_whole=[0]), [("a", 0.0, 240.0), *CUT[1:]], [8, 6, 10]),
        (choose(keep_whole=[0, 3]), [("a", 0.0, 240.0), *CUT[1:3], ("d", 0.0, 240.0)], [8, 6, 10]),
        (choose(keep_whole=[0, 1, 2, 3]), FULL, [8, 6, 10]),
        (choose(hard_cut_in=[1]), CUT, [0, 6, 10]),
        (choose(hard_cut_out=[1]), CUT, [8, 0, 10]),
        (choose(hard_cut_in=[1], hard_cut_out=[1]), CUT, [0, 0, 10]),
        (choose(hard_cut_in=[0], hard_cut_out=[3]), CUT, [8, 6, 10]),  # the mix's own edges
        (choose(blend=False, hard_cut_in=[1]), CUT, [0, 0, 0]),
    ],
)
def test_each_switch_and_per_song_choice(
    choices: MixChoices, tracks: list[Any], crossfades: list[int]
) -> None:
    good = applied(proposal(PLAIN), FACTS, choices)
    assert windows(good) == tracks
    assert good["crossfades"] == crossfades
    assert mix_plan_policy.checked(good, ORDER, FACTS) == good


@pytest.mark.parametrize("choices", [choose(keep_whole=[0]), choose(trim=False)])
def test_a_non_adjacent_repeat_collapses_to_its_first_place(choices: MixChoices) -> None:
    good = applied(proposal(REPEAT), FACTS, choices)
    assert [r for r, _, _ in windows(good)] == ["a", "b", "c", "d"]
    assert windows(good)[0] == ("a", 0.0, 240.0)
    # a -> b was adjacent in the proposal (kept unless b is whole and Trim is off, still kept);
    # b -> c is a join made by dropping the repeat: 0
    assert good["crossfades"] == [8, 0, 10]


@pytest.mark.parametrize("choices", [choose(keep_whole=[0]), choose(trim=False)])
def test_a_loop_run_collapses_and_no_loop_refusal_is_left(choices: MixChoices) -> None:
    good = applied(proposal(LOOP), FACTS, choices)
    assert [r for r, _, _ in windows(good)] == ["a", "b", "c", "d"]
    assert windows(good)[0] == ("a", 0.0, 240.0)
    assert good["crossfades"] == [0, 6, 10]  # a's last piece -> b is a new join: 0


def test_repeats_and_loops_stay_with_trim_on() -> None:
    assert applied(proposal(REPEAT), FACTS, DJ) == REPEAT
    assert applied(proposal(LOOP), FACTS, DJ) == LOOP


@pytest.mark.parametrize(
    ("timing", "reason"),
    [
        ("approximate", "timing_approximate"),
        ("unusable", "timing_unusable"),
        (None, "timing_unknown"),
    ],
)
def test_a_track_without_exact_timing_is_always_whole_with_its_reason(
    timing: Any, reason: str
) -> None:
    facts = {**FACTS, "b": track(timing)}
    for choices in (DJ, choose(keep_whole=[0]), choose(hard_cut_in=[1])):
        good = applied(proposal(PLAIN), facts, choices)
        assert windows(good)[1] == ("b", 0.0, 240.0)
        assert windows(good)[0] == (("a", 0.0, 240.0) if choices.keep_whole else CUT[0])
    shown = suggestions(proposal(PLAIN), facts)
    assert [s.full_only for s in shown] == [None, reason, None, None]
    # crossfades into and out of it still follow Blend
    assert applied(proposal(PLAIN), facts, DJ)["crossfades"] == [8, 6, 10]


def test_a_whole_song_under_60_s_ends_window_too_short_from_checked() -> None:
    short = track("exact", 50.0, 10.0, 40.0)
    facts = {"a": FACTS["a"], "e": short}
    looped = spec(("a", 0.0, 240.0), ("e", 0.0, 40.0), ("e", 10.0, 50.0), crossfades=[8, 0])
    value = Proposal(("a", "e"), looped, 300)
    assert mix_plan_policy.checked(looped, ("a", "e"), facts) == looped  # a valid proposal
    assert applied(value, facts, DJ) == looped
    assert refused(value, choose(keep_whole=[1]), facts).reason == "window_too_short"
    assert refused(value, WHOLE, facts).reason == "window_too_short"


@pytest.mark.parametrize(
    ("value", "choices"),
    [
        (proposal(PLAIN, target=600), WHOLE),  # 960 s over 600 s (over by 60 %)
        (proposal(PLAIN, target=1000), DJ),  # 796 s under 1000 s (under by 20 %)
    ],
)
def test_out_of_tolerance_both_ways(value: Proposal, choices: MixChoices) -> None:
    caught = refused(value, choices)
    assert caught.reason == "class_length_out_of_tolerance"
    assert caught.total_sec == pytest.approx(composed_sec(applied(proposal(PLAIN), FACTS, choices)))
    assert caught.allowed == allowed(value.target_sec)


def _whole_of(total: float) -> tuple[dict[str, TrackFacts], Proposal]:
    each = total / 4
    facts = {r: track("exact", each) for r in ORDER}
    return facts, proposal(spec(*((r, 0.0, each) for r in ORDER), crossfades=[0, 0, 0]))


def test_the_class_bounds_are_the_ten_percent_under_and_the_upper_bound() -> None:
    facts, value = _whole_of(900.0)
    # 10 % under: 900 s is the lowest a 1000 s class allows
    assert composed_sec(applied(Proposal(ORDER, value.spec, 1000), facts, WHOLE)) == 900.0
    caught = refused(Proposal(ORDER, value.spec, 1001), WHOLE, facts)
    assert caught.reason == "class_length_out_of_tolerance"
    # over: the smallest target whose upper bound reaches 900 s accepts it, one less refuses it
    lowest = math.ceil(900 / (1 + length_policy.UPPER))
    applied(Proposal(ORDER, value.spec, lowest), facts, WHOLE)
    caught = refused(Proposal(ORDER, value.spec, lowest - 1), WHOLE, facts)
    assert caught.reason == "class_length_out_of_tolerance"


def test_whole_songs_hold_without_dj_mix_even_if_the_model_check_was_bypassed() -> None:
    forged = MixChoices.model_construct(dj_mix=False, blend=True, trim=True)
    good = applied(proposal(PLAIN), FACTS, forged)
    assert windows(good) == FULL


def test_invalid_input_comes_first() -> None:
    caught = refused(proposal(PLAIN, target=10_000), choose(keep_whole=[4]))
    assert caught.reason == "invalid_input"


def test_is_plain() -> None:
    assert is_plain(applied(proposal(PLAIN), FACTS, WHOLE), LENGTHS)
    assert is_plain(applied(proposal(REPEAT), FACTS, WHOLE), LENGTHS)
    assert is_plain(applied(proposal(LOOP), FACTS, WHOLE), LENGTHS)
    cut = spec(("a", 0.0, 180.0), *((r, 0.0, 240.0) for r in "bcd"), crossfades=[0, 0, 0])
    late = spec(("a", 2.0, 240.0), *((r, 0.0, 240.0) for r in "bcd"), crossfades=[0, 0, 0])
    faded = spec(*((r, 0.0, 240.0) for r in ORDER), crossfades=[0, 1, 0])
    twice = spec(*((r, 0.0, 240.0) for r in "abcda"), crossfades=[0, 0, 0, 0])
    unknown = spec(*((r, 0.0, 240.0) for r in "abcz"), crossfades=[0, 0, 0])
    for value in (cut, late, faded, twice, unknown):
        assert not is_plain(value, LENGTHS)
    assert is_plain(spec(*((r, 0.0, 240.0) for r in ORDER), crossfades=[0, 0, 0]), LENGTHS)


def test_suggestions_show_each_song_with_its_pieces_and_phrase_ends() -> None:
    shown = suggestions(proposal(REPEAT), FACTS)
    assert [s.position for s in shown] == [0, 1, 2, 3]
    assert shown[0].pieces == ((0.0, 100.0), (120.0, 240.0))
    assert shown[0].ends_on_phrase == (False, False)  # 100 is no phrase point; 240 the file's end
    assert shown[0].length == 240.0
    assert shown[0].timing_class == "exact"
    assert suggestions(proposal(PLAIN), FACTS)[0].ends_on_phrase == (True,)  # 180 is one
    approximate = {**FACTS, "a": track("approximate")}  # a phrase point it cannot trust
    assert suggestions(proposal(PLAIN), approximate)[0].ends_on_phrase == (False,)


def _random_choices(rng: random.Random) -> MixChoices:
    if rng.random() < 0.15:
        return WHOLE

    def some() -> list[int]:
        return sorted(rng.sample(range(4), rng.randint(0, 4)))

    return choose(
        blend=rng.random() < 0.7,
        trim=rng.random() < 0.8,
        keep_whole=some(),
        hard_cut_in=some(),
        hard_cut_out=some(),
    )


REASONS = {
    "class_length_out_of_tolerance",
    *("window_too_short", "crossfade_too_long", "crossfades_overlap", "mix_too_long"),
}


@pytest.mark.parametrize("base", [PLAIN, REPEAT, LOOP])
@pytest.mark.parametrize("target", [880, 1000])
def test_random_choices_are_checked_deterministic_and_idempotent(
    base: dict[str, Any], target: int
) -> None:
    rng = random.Random(1367)  # noqa: S311 - a reproducible table, not a secret
    outcomes: set[str] = set()
    for _ in range(150):
        choices = _random_choices(rng)
        try:
            good = applied(proposal(base, target), FACTS, choices)
        except MixChoiceRefused as exc:
            outcomes.add(exc.reason)
            continue
        outcomes.add("ok")
        assert mix_plan_policy.checked(good, ORDER, FACTS) == good
        again = applied(proposal(base, target), FACTS, choices)
        assert json.dumps(again, sort_keys=True) == json.dumps(good, sort_keys=True)
        assert applied(proposal(good, target), FACTS, choices) == good  # idempotent
        if choices == WHOLE:
            assert is_plain(good, LENGTHS)
    assert "ok" in outcomes
    assert outcomes - {"ok"} <= REASONS  # every refusal is one of ``checked``'s or B6's
