"""The game rules of ADR 0029 section 2, one row each (issue #878). Pure: no database or network.

classbeat adds no 10 s rule: G3 and G4 are the library's and are tested in
``test_mowl_game_rules.py``. This file pins who enforces each row and what the retired-game
check does.
"""

import copy
from typing import Any

import pytest
from mowl import workout

from classbeat.agent import games_catalog
from classbeat.design import games_policy

# (row, who enforces it, where it is tested)
ROWS = [
    ("G1", "the strict Game and the catalogue", "test_games_catalog.py"),
    ("G2", "a notice on the page; not checked", "frontend"),
    ("G3", "workout.validate", "test_mowl_game_rules.py"),
    ("G4", "workout.validate", "test_mowl_game_rules.py"),
    ("G5", "nothing: no maximum, count, recovery or spacing", "test_games_add_no_limits"),
    ("G6", "prompt guidance", "test_agent_game_prompt.py"),
    ("G7", "MOWL runtime; not implemented", "-"),
    ("G8", "prompt guidance", "test_agent_game_prompt.py"),
    ("G9", "prompt guidance", "test_agent_game_prompt.py"),
    ("G10", "not implemented", "-"),
    ("G11", "revision-chat eval cases", "test_evals_suite_design.py"),
]

SPEC: dict[str, Any] = {
    "name": "Test",
    "segments": [
        {"name": "Warm-up", "kind": "warmup", "intervals": [{"sec": 120, "zone": 1}]},
        {"name": "Main", "kind": "main", "intervals": []},
        {"name": "Cooldown", "kind": "cooldown", "intervals": [{"sec": 60, "zone": 1}]},
    ],
}


def spec_with(*main: dict[str, Any]) -> dict[str, Any]:
    spec = copy.deepcopy(SPEC)
    spec["segments"][1]["intervals"] = list(main)
    return spec


def game(sec: int, name: str = "racetrack") -> dict[str, Any]:
    return {"sec": sec, "zone": 6, "game": name}


def test_every_row_of_the_table_is_listed_once() -> None:
    assert [r[0] for r in ROWS] == [f"G{n}" for n in range(1, 12)]


def test_the_library_is_the_only_10_second_rule() -> None:
    workout.validate(spec_with(game(10), {"sec": 10, "zone": 2}))  # G3 and G4 at the limit
    with pytest.raises(ValueError, match="at least 10"):
        workout.validate(spec_with(game(9), {"sec": 60, "zone": 2}))
    with pytest.raises(ValueError, match="after a game"):
        workout.validate(spec_with(game(30), {"sec": 9, "zone": 2}))


def test_back_to_back_games_are_refused() -> None:
    with pytest.raises(ValueError, match="separated by a regular interval"):
        workout.validate(spec_with(game(30), game(30, "overflow"), {"sec": 60, "zone": 2}))
    workout.validate(spec_with(game(30), {"sec": 60, "zone": 2}, game(30, "overflow")))


def test_games_add_no_limits() -> None:
    games = [game(10, t) for t in games_catalog.TYPES] * 3
    workout.validate(spec_with(*[x for g in games for x in (g, {"sec": 60, "zone": 2})]))


def test_a_game_outside_the_four_is_refused() -> None:
    with pytest.raises(ValueError, match="game"):
        workout.validate(spec_with(game(30, "ghost_target"), {"sec": 60, "zone": 2}))


def legacy() -> dict[str, Any]:
    return spec_with(game(30, "ghost_target"), {"sec": 60, "zone": 2})


@pytest.mark.parametrize("name", games_catalog.TYPES)
def test_a_current_game_is_not_retired(name: str) -> None:
    content = spec_with(game(30, name), {"sec": 60, "zone": 2})
    assert not games_policy.game_retired(content)
    assert not games_policy.game_retired(content, "cycling")


def test_a_legacy_game_is_retired_in_a_cycling_class_only() -> None:
    assert games_policy.game_retired(legacy())
    assert games_policy.game_retired(legacy(), "cycling")
    assert not games_policy.game_retired(legacy(), "running")  # running has no games


@pytest.mark.parametrize("content", [None, {}, [], {"segments": "x"}, {"segments": [1, {}]}])
def test_malformed_content_is_not_retired(content: object) -> None:
    assert not games_policy.game_retired(content)
