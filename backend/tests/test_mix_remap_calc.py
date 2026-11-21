"""ADR 0036 amendment B4a (#1367): the proposal's workout moved onto the derived mix timeline by
song anchor (``mix_remap_calc``)."""

import itertools
import random
from typing import Any

import pytest

from classbeat.studio import mix_plan_policy
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, parsed
from classbeat.studio.mix_choice_policy import applied, composed_sec
from classbeat.studio.mix_remap_calc import boundaries, kept, remapped
from mix_choice_fixtures import FACTS, LOOP, ORDER, PLAIN, REPEAT, proposal, spec, track

DJ = MixChoices.dj_defaults()
# PLAIN lays out: a 0-180, b from 172 (8 s overlap), c from 366, d from 596, end 796.
KEEP_A = parsed({"dj_mix": True, "blend": True, "trim": True, "keep_whole": [0]})
# PLAIN with a whole: a 0-240, b from 232, c from 426, d from 656, end 856.


def derived(base: dict[str, Any], choices: MixChoices, facts: Any = FACTS) -> dict[str, Any]:
    return applied(proposal(base), facts, choices)


def half_up(cs: int) -> int:
    return (cs + 50) // 100


def does_not_fit(base: dict[str, Any], new: dict[str, Any], secs: list[int]) -> str:
    with pytest.raises(MixChoiceRefused) as caught:
        remapped(base, new, secs)
    return caught.value.reason


def test_the_start_stays_at_0_and_a_whole_first_song_s_intro_lengthens_the_first_interval() -> None:
    intro = spec(
        ("a", 20.0, 200.0), ("b", 0.0, 240.0), ("c", 0.0, 240.0), ("d", 0.0, 240.0),
        crossfades=[8, 8, 8],
    )  # fmt: skip
    assert mix_plan_policy.checked(intro, ORDER, FACTS) == intro
    new = derived(intro, KEEP_A)  # a now plays from 0: 20 s more before every effort
    secs = [30, 300, 300, 246]  # ends on the music: 876 s
    # 20 s of intro in the first interval, a's 40 s of outro in the second (it holds a's end)
    assert remapped(intro, new, secs) == [50, 340, 300, 246]
    assert sum(remapped(intro, new, secs)) == half_up(round(composed_sec(new) * 100))


def test_a_boundary_in_an_overlap_belongs_to_the_incoming_piece() -> None:
    # 175 s is in a -> b's overlap (172 to 180): b's second 23, which keeping a whole moves to 235
    secs = [60, 115, 125, 200, 230, 66]
    assert boundaries(PLAIN, PLAIN, secs)[1] == 175 * 100  # identity: the same moment of b
    assert remapped(PLAIN, derived(PLAIN, KEEP_A), secs) == [60, 175, 125, 200, 230, 66]


@pytest.mark.parametrize("base", [PLAIN, REPEAT, LOOP])
def test_checked_is_a_fixed_point_so_the_proposal_s_own_choices_move_nothing(
    base: dict[str, Any],
) -> None:
    assert derived(base, DJ) == base
    end = round(composed_sec(base))
    rng = random.Random(1367)  # noqa: S311 - a reproducible table, not a secret
    for _ in range(40):
        cuts = sorted(rng.sample(range(5, end - 5, 5), rng.randint(1, 20)))
        secs = [b - a for a, b in itertools.pairwise([0, *cuts, end])]
        assert remapped(base, base, secs) == secs


def test_the_clamp_keeps_a_kept_piece_inside_its_derived_own_part() -> None:
    # b's end placed 10 s earlier and its crossfade into c set to 0: b plays 172 to 362, c from 362
    moved = spec(
        ("a", 0.0, 180.0), ("b", 20.0, 210.0), ("c", 0.0, 240.0), ("d", 40.0, 240.0),
        crossfades=[8, 0, 10],
    )  # fmt: skip
    assert mix_plan_policy.checked(moved, ORDER, FACTS) == moved
    # 365 s is b's second 213, past b's new end: clamped to 362, not 365, before c's 362
    found = boundaries(PLAIN, moved, [365, 1, 430])
    assert found[:2] == [362 * 100, 362 * 100]
    assert found == sorted(found)


def test_an_effort_on_a_kept_whole_song_stays_on_the_same_source_second() -> None:
    # c starts at 366 in the proposal and at 426 once a is whole: its second 134 is 500 -> 560
    assert boundaries(PLAIN, derived(PLAIN, KEEP_A), [500, 296]) == [560 * 100, 856 * 100]


def test_a_dropped_repeat_s_boundary_goes_to_the_next_kept_piece() -> None:
    # REPEAT: a 0-100, b from 92, a again from 286, c from 398, d from 628, end 828.
    # a whole: a 0-240, b from 232, c from 432 (a hard join), d from 662, end 862.
    new = derived(REPEAT, KEEP_A)
    secs = [60, 240, 150, 150, 228]  # 300 is in the dropped repeat
    assert boundaries(REPEAT, new, secs) == [6000, 43200, 48400, 63400, 86200]
    assert remapped(REPEAT, new, secs) == [60, 372, 52, 150, 228]
    # two boundaries in the dropped repeat land on one point: always refused (B4a step 5)
    assert does_not_fit(REPEAT, new, [60, 240, 50, 50, 428]) == "workout_does_not_fit"


def test_a_dropped_last_piece_s_boundaries_go_to_the_derived_end() -> None:
    closing = spec(
        ("a", 0.0, 100.0), ("b", 20.0, 220.0), ("c", 0.0, 240.0), ("d", 40.0, 240.0),
        ("a", 120.0, 240.0), crossfades=[8, 6, 10, 8],
    )  # fmt: skip  # a again from 708 to the end, 828
    assert mix_plan_policy.checked(closing, ORDER, FACTS) == closing
    new = derived(closing, KEEP_A)  # a 0-240, b 232, c 426, d 656, end 856
    # 600 is d's second 124 (740 now); 760 is in the dropped piece: the derived end, as is the end
    assert boundaries(closing, new, [600, 160, 68]) == [74000, 85600, 85600]
    assert does_not_fit(closing, new, [600, 160, 68]) == "workout_does_not_fit"
    assert remapped(closing, new, [600, 228]) == [740, 116]  # no boundary in it: it fits


def test_rounded_boundaries_give_a_total_equal_to_the_rounded_end() -> None:
    # c and d last 240.37 and 240.49 s: whole, the derived end is 936.86 s
    facts = {**FACTS, "c": track(length=240.37), "d": track(length=240.49)}
    assert mix_plan_policy.checked(PLAIN, ORDER, facts) == PLAIN
    whole = parsed({"dj_mix": True, "blend": True, "trim": False})
    new = applied(proposal(PLAIN), facts, whole)
    end = round(composed_sec(new) * 100)
    secs = [61] * 12 + [64]  # 13 intervals, each moved by a fraction of a second
    lengths = remapped(PLAIN, new, secs)
    assert sum(lengths) == half_up(end) == 937
    found = boundaries(PLAIN, new, secs)
    assert list(itertools.accumulate(lengths)) == [half_up(b) for b in found]


def test_a_boundary_on_a_half_second_rounds_up() -> None:
    facts = {**FACTS, "d": track(length=240.5)}
    whole = parsed({"dj_mix": True, "blend": True, "trim": False})
    new = applied(proposal(PLAIN), facts, whole)
    assert composed_sec(new) == 936.5
    assert remapped(PLAIN, new, [796]) == [937]  # half up, never to the even second


def test_new_intro_and_outro_music_lengthen_the_interval_it_falls_in() -> None:
    keep_d = parsed({"dj_mix": True, "blend": True, "trim": True, "keep_whole": [3]})
    # d plays from 0, not 40: 40 s more in the interval over d's start (590 to 796)
    assert remapped(PLAIN, derived(PLAIN, keep_d), [590, 206]) == [590, 246]
    # a plays to 240, not 180: the interval over a's end (60 to 175) gains 60 s
    assert remapped(PLAIN, derived(PLAIN, KEEP_A), [60, 115, 621]) == [60, 175, 621]


def test_the_end_goes_to_the_derived_end_only_within_1_s_of_the_proposal_s_end() -> None:
    new = derived(PLAIN, KEEP_A)  # the proposal ends at 796 s, the derived timeline at 856 s
    assert remapped(PLAIN, new, [60, 115, 620]) == [60, 175, 621]  # 795 s: the derived end
    assert remapped(PLAIN, new, [60, 115, 619]) == [60, 175, 619]  # 794 s: d's second 238


def test_a_squeezed_interval_is_workout_does_not_fit() -> None:
    tight = spec(
        ("a", 0.0, 180.0), ("b", 20.0, 200.0), ("c", 0.0, 240.0), ("d", 40.0, 240.0),
        crossfades=[8, 6, 10],
    )  # fmt: skip  # b ends 20 s earlier: it plays alone to its second 194, 346 s in the mix
    # 350 and 360 are b's seconds 198 and 208, both past it: both 346, an interval of 0 s
    assert boundaries(PLAIN, tight, [350, 10, 436])[:2] == [34600, 34600]
    assert does_not_fit(PLAIN, tight, [350, 10, 436]) == "workout_does_not_fit"
    assert remapped(PLAIN, PLAIN, [5, 791]) == [5, 791]  # 5 s is the shortest that fits
    assert does_not_fit(PLAIN, PLAIN, [4, 792]) == "workout_does_not_fit"  # under MIN_INTERVAL


def test_kept_matches_the_derivation_and_refuses_anything_else() -> None:
    assert kept(["a", "b", "a", "c"], ["a", "b", "c"]) == [0, 1, None, 2]
    assert kept(["a", "a", "b"], ["a", "b"]) == [0, None, 1]
    assert kept(["a", "b", "c", "d", "a"], ["a", "b", "c", "d"]) == [0, 1, 2, 3, None]
    with pytest.raises(ValueError, match="derivation"):
        kept(["a", "b"], ["b", "a"])


def _random(rng: random.Random) -> MixChoices:
    def some() -> list[int]:
        return sorted(rng.sample(range(4), rng.randint(0, 4)))

    on = rng.random() < 0.85
    return parsed(
        {"dj_mix": on, "blend": on and rng.random() < 0.7, "trim": on and rng.random() < 0.8}
        | ({"keep_whole": some(), "hard_cut_in": some(), "hard_cut_out": some()} if on else {})
    )


@pytest.mark.parametrize("base", [PLAIN, REPEAT, LOOP])
def test_random_choices_keep_the_map_monotone_and_the_total_on_the_music(
    base: dict[str, Any],
) -> None:
    rng = random.Random(4)  # noqa: S311 - a reproducible table, not a secret
    end = round(composed_sec(base))
    outcomes: set[str] = set()
    for _ in range(200):
        new = derived(base, _random(rng))
        cuts = sorted(rng.sample(range(1, end), rng.randint(0, 25)))
        secs = [b - a for a, b in itertools.pairwise([0, *cuts, end])]
        found = boundaries(base, new, secs)
        assert found == sorted(found)  # boundaries never cross
        assert found[-1] == round(composed_sec(new) * 100)  # it ended on its music
        try:
            lengths = remapped(base, new, secs)
        except MixChoiceRefused as exc:
            outcomes.add(exc.reason)
            continue
        outcomes.add("ok")
        assert sum(lengths) == half_up(found[-1])
    assert outcomes == {"ok", "workout_does_not_fit"}
