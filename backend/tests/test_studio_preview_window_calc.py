"""ADR 0042 section 6.4 (#1392): ``preview_window_calc``, where a preview window goes. Pure, so the
rules are table tests and seeded random mixes: a selected transition is always whole, no window is
under 60 s except at the mix end, an end moves later out of a guard zone and never earlier, no
window is over 6 minutes, and the windows of a session are contiguous."""

import random
from itertools import pairwise
from typing import Any

import pytest

from classbeat.studio import preview_window_calc as calc
from classbeat.studio import render_policy
from classbeat.studio.preview_window_calc import Transition, Window

S = render_policy.RATE
G = calc.GUARD_FRAMES


def track(dst: int, length: int, fade_in: int = 0, fade_out: int = 0) -> dict[str, Any]:
    return {
        "path": "a.mp3",
        "src_start": 0,
        "src_end": length,
        "dst_start": dst,
        "fade_in": fade_in,
        "fade_out": fade_out,
    }


def chain(lengths: list[int], fades: list[int]) -> list[dict[str, Any]]:
    """Tracks that crossfade (fade ``fades[i]``, 0 for a hard cut) from one to the next."""
    tracks: list[dict[str, Any]] = []
    dst = 0
    for n, length in enumerate(lengths):
        fade_in = fades[n - 1] if n else 0
        fade_out = fades[n] if n < len(fades) else 0
        tracks.append(track(dst, length, fade_in, fade_out))
        dst += length - fade_out
    return tracks


def random_mix(rng: random.Random, dense: bool) -> list[dict[str, Any]]:
    """Long tracks, or (``dense``) short ones, so that guard zones touch and chain."""
    count = rng.randint(2, 80 if dense else 40)
    lengths = [rng.randint(*((12, 40) if dense else (20, 8 * 60))) * S for _ in range(count)]
    fades = [rng.choice([0, 0, rng.randint(1, 20) * S]) for _ in range(count - 1)]
    tracks = chain(lengths, fades)
    for n in range(1, count):  # some gaps of silence
        if rng.random() < 0.1:
            gap = rng.randint(1, 5) * S
            for later in tracks[n:]:
                later["dst_start"] += gap
    return tracks


def valid_mix(rng: random.Random, dense: bool = False) -> list[dict[str, Any]]:
    """A random mix that ``render_policy`` accepts."""
    while True:
        tracks = random_mix(rng, dense)
        try:
            render_policy.validate_plan({"output": "o.mp3", "tracks": tracks})
        except ValueError:
            continue
        return tracks


# -- the transitions and the guard zones ---------------------------------------------------------


def test_a_crossfade_a_hard_cut_and_a_gap_are_transitions() -> None:
    fade = chain([100 * S, 100 * S], [10 * S])
    assert calc.transitions(fade) == [Transition(90 * S, 100 * S)]
    cut = chain([100 * S, 100 * S], [0])
    assert calc.transitions(cut) == [Transition(100 * S, 100 * S)]
    gap = [track(0, 100 * S), track(104 * S, 50 * S)]
    assert calc.transitions(gap) == [Transition(100 * S, 104 * S)]
    assert calc.transitions([track(0, S)]) == []


def test_the_guard_zones_are_each_transition_plus_and_minus_15_s_and_touching_ones_chain() -> None:
    zones = calc.guard_zones([Transition(100 * S, 110 * S), Transition(300 * S, 300 * S)])
    assert zones == [Window(85 * S, 125 * S), Window(285 * S, 315 * S)]
    touching = calc.guard_zones([Transition(100 * S, 110 * S), Transition(140 * S, 140 * S)])
    assert touching == [Window(85 * S, 155 * S)]  # 125 s meets 125 s
    assert calc.guard_zones([]) == []


def test_review_start_is_the_fade_start_less_15_s() -> None:
    assert calc.review_start(Transition(100 * S, 110 * S)) == 85 * S
    assert calc.review_start(Transition(5 * S, 8 * S)) == 0


# -- the rules, one by one -----------------------------------------------------------------------


def test_the_nominal_window_is_four_minutes() -> None:
    tracks = chain([600 * S], [])
    assert calc.place_window(tracks, 30 * S) == Window(30 * S, 270 * S)


def test_an_end_in_a_guard_zone_moves_later_to_the_zone_end() -> None:
    # a crossfade 240..250 s: its zone is 225..265 s, and the nominal end 240 s is inside it
    tracks = chain([250 * S, 600 * S], [10 * S])
    assert calc.transitions(tracks) == [Transition(240 * S, 250 * S)]
    assert calc.place_window(tracks, 0) == Window(0, 265 * S)
    assert calc.place_window(tracks, 10 * S).end == 265 * S  # 250 s is inside as well
    assert calc.place_window(tracks, 30 * S).end == 270 * S  # 270 s is outside: unchanged


def test_an_end_exactly_on_a_zone_edge_stays() -> None:
    tracks = chain([310 * S, 600 * S], [10 * S])  # the crossfade 300..310 s: zone 285..325 s
    assert calc.guard_zones(calc.transitions(tracks)) == [Window(285 * S, 325 * S)]
    assert calc.place_window(tracks, 45 * S).end == 285 * S  # on the zone's start: not inside
    assert calc.place_window(tracks, 46 * S).end == 325 * S  # one second in: to the zone's end
    assert calc.place_window(tracks, 85 * S).end == 325 * S  # on the zone's end: stays
    assert calc.place_window(tracks, 86 * S).end == 326 * S  # outside: unchanged


def test_a_chain_of_touching_zones_moves_the_end_past_all_of_them() -> None:
    # hard cuts at 240, 270 and 300 s: zones 225..255, 255..285, 285..315 touch
    tracks = chain([240 * S, 30 * S, 30 * S, 600 * S], [0, 0, 0])
    assert calc.guard_zones(calc.transitions(tracks)) == [Window(225 * S, 315 * S)]
    assert calc.place_window(tracks, 0) == Window(0, 315 * S)


def test_a_window_is_never_over_six_minutes_and_may_then_end_in_a_zone() -> None:
    # hard cuts every 20 s from 220 s: no end from 4 min to 6 min is outside a zone
    lengths = [220 * S, *[20 * S] * 12, 600 * S]
    tracks = chain(lengths, [0] * (len(lengths) - 1))
    window = calc.place_window(tracks, 0)
    assert window == Window(0, 360 * S)
    assert any(z.start < window.end < z.end for z in calc.guard_zones(calc.transitions(tracks)))


def test_a_selected_transition_is_whole_with_15_s_after_it() -> None:
    tracks = chain([600 * S, 600 * S], [10 * S])  # the crossfade 590..600 s
    selected = calc.transitions(tracks)[0]
    start = calc.review_start(selected)
    assert start == 575 * S
    assert calc.place_window(tracks, start, selected) == Window(575 * S, 815 * S)
    short = calc.place_window(tracks, start)
    assert short.end == 815 * S  # nominal: the same here, the transition is early in it


def test_a_selected_transition_late_in_a_window_extends_it() -> None:
    tracks = chain([600 * S, 600 * S], [10 * S])
    selected = Transition(590 * S, 600 * S)
    window = calc.place_window(tracks, 300 * S, selected)  # nominal 300..540 s ends before it
    assert window.start <= selected.start
    assert window.end == 615 * S  # the fade end plus 15 s


def test_a_selected_transition_that_cannot_be_whole_is_refused() -> None:
    tracks = chain([600 * S, 600 * S], [10 * S])
    selected = Transition(590 * S, 600 * S)
    with pytest.raises(ValueError, match="selected"):
        calc.place_window(tracks, 595 * S, selected)  # it starts before the window
    with pytest.raises(ValueError, match="selected"):
        calc.place_window(tracks, 200 * S, selected)  # 415 s needed: over 6 min


def test_a_window_at_the_mix_end_may_be_short_and_ends_at_the_mix_end() -> None:
    tracks = chain([300 * S], [])
    assert calc.place_window(tracks, 280 * S) == Window(280 * S, 300 * S)
    assert calc.place_window(tracks, 100 * S) == Window(100 * S, 300 * S)  # nominal passes the end


def test_a_zone_that_runs_past_the_mix_end_ends_the_window_at_the_mix_end() -> None:
    tracks = chain([260 * S, 20 * S], [15 * S])  # a crossfade 245..260 s, the mix ends at 265 s
    assert calc.guard_zones(calc.transitions(tracks)) == [Window(230 * S, 275 * S)]
    assert calc.place_window(tracks, 5 * S).end == 265 * S


def test_a_start_outside_the_mix_is_refused() -> None:
    tracks = chain([300 * S], [])
    for bad in (-1, 300 * S, 400 * S):
        with pytest.raises(ValueError, match="start"):
            calc.place_window(tracks, bad)


def test_the_next_window_starts_where_the_previous_ends() -> None:
    tracks = chain([300 * S, 300 * S, 300 * S], [10 * S, 10 * S])
    windows = list(calc.windows_from(tracks, 0))
    assert windows[0].start == 0
    for a, b in pairwise(windows):
        assert b.start == a.end
    assert windows[-1].end == render_policy.composed_frames(tracks)
    assert calc.next_window(tracks, windows[-1]) is None
    assert calc.next_window(tracks, windows[0]) == windows[1]


# -- the properties, over random mixes -----------------------------------------------------------

SEEDS = range(40)


def in_zone(end: int, zones: list[Window]) -> bool:
    return any(z.start < end < z.end for z in zones)


@pytest.mark.parametrize("dense", [False, True])
@pytest.mark.parametrize("seed", SEEDS)
def test_properties_of_the_placement_over_random_mixes(seed: int, dense: bool) -> None:
    rng = random.Random(seed)  # noqa: S311 - a reproducible table, not a secret
    tracks = valid_mix(rng, dense)
    total = render_policy.composed_frames(tracks)
    found = calc.transitions(tracks)
    zones = calc.guard_zones(found)
    for _ in range(25):
        start = rng.randrange(0, total)
        window = calc.place_window(tracks, start)
        assert window.start == start
        assert window.end <= total
        assert window.end - window.start <= calc.MAX_FRAMES  # no window over 6 min
        assert window.end - window.start >= min(calc.MIN_FRAMES, total - start)
        if window.end != total:
            assert window.end - window.start >= calc.MIN_FRAMES  # only the mix end is short
        assert window.end >= min(start + calc.NOMINAL_FRAMES, total)  # extended, never shortened
        if window.end not in (total, start + calc.MAX_FRAMES):
            assert not in_zone(window.end, zones)  # out of the guard zones
        assert render_policy.validate_plan(
            {
                "output": "o.mp3",
                "purpose": "preview",
                "tracks": tracks,
                "window": {"start": window.start, "end": window.end},
            }
        )


@pytest.mark.parametrize("dense", [False, True])
@pytest.mark.parametrize("seed", SEEDS)
def test_a_selected_transition_is_always_whole_over_random_mixes(seed: int, dense: bool) -> None:
    rng = random.Random(1000 + seed)  # noqa: S311 - a reproducible table, not a secret
    tracks = valid_mix(rng, dense)
    total = render_policy.composed_frames(tracks)
    for selected in calc.transitions(tracks):
        for before in (
            G,
            G + rng.randint(0, 300 * S),
        ):  # review_start, or a window that began earlier
            start = max(selected.start - before, 0)
            if selected.end + G - start > calc.MAX_FRAMES:
                continue
            window = calc.place_window(tracks, start, selected)
            assert window.start <= selected.start
            assert window.end >= min(selected.end + G, total)
            assert window.end - window.start <= calc.MAX_FRAMES
        review = calc.place_window(tracks, calc.review_start(selected), selected)
        assert review.start == max(selected.start - G, 0)
        assert review.end >= min(selected.end + G, total)


@pytest.mark.parametrize("dense", [False, True])
@pytest.mark.parametrize("seed", SEEDS)
def test_the_windows_of_a_session_are_contiguous_over_random_mixes(seed: int, dense: bool) -> None:
    rng = random.Random(2000 + seed)  # noqa: S311 - a reproducible table, not a secret
    tracks = valid_mix(rng, dense)
    total = render_policy.composed_frames(tracks)
    start = rng.randrange(0, total)
    windows = list(calc.windows_from(tracks, start))
    assert windows[0].start == start
    assert windows[-1].end == total
    for a, b in pairwise(windows):
        assert b.start == a.end
    for w in windows[:-1]:
        assert w.end - w.start >= calc.MIN_FRAMES  # only the last one, at the mix end, may be short
    assert all(w.end > w.start for w in windows)
