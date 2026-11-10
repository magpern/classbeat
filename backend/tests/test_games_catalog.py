"""The four-game catalogue (ADR 0029 section 2, G1; issue #878). Pure: no database or network."""

from typing import get_args

import pytest
from mowl import workout

from classbeat.agent import games_catalog
from classbeat.agent.schemas import Game


def test_exactly_the_four_games_of_the_strict_schema() -> None:
    assert get_args(Game) == games_catalog.TYPES
    assert [g.type for g in games_catalog.CATALOG] == [
        "racetrack",
        "power_pull",
        "fast_lane",
        "overflow",
    ]
    assert set(games_catalog.BY_TYPE) == set(workout.GAMES)


def test_the_mowl_ids_are_the_librarys() -> None:
    assert {g.type: g.mowl_id for g in games_catalog.CATALOG} == {
        "racetrack": 1,
        "power_pull": 4,
        "fast_lane": 5,
        "overflow": 6,
    }
    assert all(g.mowl_id == workout.GAMES[g.type] for g in games_catalog.CATALOG)


def test_each_game_has_the_i18n_key_the_page_uses_a_description_and_advice() -> None:
    for g in games_catalog.CATALOG:
        assert g.i18n_key == f"class.game.{g.type}"
        assert g.name
        assert g.article.endswith(".")
        assert g.advice


def test_the_prompt_lines_name_each_game_and_mark_the_advice_as_ours() -> None:
    lines = games_catalog.prompt_lines().splitlines()
    assert len(lines) == 4
    for line, g in zip(lines, games_catalog.CATALOG, strict=True):
        assert line.startswith(f'- "{g.type}" ({g.name}): {g.article}')
        assert f"(classbeat advice, not MOWL fact: {g.advice})" in line
    assert "ghost" not in games_catalog.prompt_lines().lower()


def test_label_names_a_catalogue_game_and_leaves_a_retired_one_as_it_is() -> None:
    assert games_catalog.label("power_pull") == "Power Pull"
    assert games_catalog.label("ghost_target") == "ghost_target"


@pytest.mark.parametrize(
    "fact", [games_catalog.LICENCE, games_catalog.HEART_RATE, games_catalog.NO_COUNTDOWN]
)
def test_the_article_facts_are_stated(fact: str) -> None:
    assert fact.endswith(".")
