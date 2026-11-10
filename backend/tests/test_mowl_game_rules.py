"""The pinned mowl-workouts enforces MOWL's game rules (ADR 0029, article facts G3 and G4).

classbeat adds no 10 s rule of its own: the library's ``validate`` is the single source, so saving
and the tool loop refuse the same drafts. These tests fail on the 0.10.0 wheel.
"""

import copy

import pytest
from mowl import workout

SPEC = {
    "name": "Test",
    "segments": [
        {"name": "Warm-up", "kind": "warmup", "intervals": [{"sec": 120, "zone": 1}]},
        {
            "name": "Main",
            "kind": "main",
            "intervals": [{"sec": 60, "zone": 4}, {"sec": 60, "zone": 2}],
        },
        {"name": "Cooldown", "kind": "cooldown", "intervals": [{"sec": 60, "zone": 1}]},
    ],
}


def spec_with(*main: dict[str, object]) -> dict[str, object]:
    spec = copy.deepcopy(SPEC)
    spec["segments"][1]["intervals"] = list(main)  # type: ignore[index]
    return spec


def test_exactly_the_four_games():
    assert dict(workout.GAMES) == {
        "racetrack": 1,
        "power_pull": 4,
        "fast_lane": 5,
        "overflow": 6,
    }


@pytest.mark.parametrize(("sec", "ok"), [(9, False), (10, True)])
def test_a_game_lasts_at_least_10_seconds(sec: int, ok: bool):
    spec = spec_with({"sec": sec, "zone": 6, "game": "power_pull"}, {"sec": 60, "zone": 2})
    if ok:
        workout.validate(spec)
    else:
        with pytest.raises(ValueError, match="at least 10"):
            workout.validate(spec)


@pytest.mark.parametrize(("sec", "ok"), [(9, False), (10, True)])
def test_the_interval_after_a_game_lasts_at_least_10_seconds(sec: int, ok: bool):
    spec = spec_with({"sec": 30, "zone": 6, "game": "overflow"}, {"sec": sec, "zone": 2})
    if ok:
        workout.validate(spec)
    else:
        with pytest.raises(ValueError, match="after a game"):
            workout.validate(spec)


def test_the_interval_after_a_game_counts_across_a_segment_boundary():
    spec = copy.deepcopy(SPEC)
    spec["segments"][0]["intervals"].append({"sec": 30, "zone": 6, "game": "racetrack"})
    spec["segments"][1]["intervals"][0]["sec"] = 9
    with pytest.raises(ValueError, match="after a game"):
        workout.validate(spec)


def test_ghost_target_is_refused():
    with pytest.raises(ValueError, match="game"):
        workout.validate(
            spec_with({"sec": 30, "zone": 6, "game": "ghost_target"}, {"sec": 60, "zone": 2})
        )
