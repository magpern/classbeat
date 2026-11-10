"""``design.revise_layer_policy`` and ``agent.stated_layer`` (ADR 0029 section 4, #904): a revise
request's stated values layered onto the class's, and the layer a revision records. Pure."""

from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.brief import Brief
from classbeat.agent.interpret_input import current_values, first_message
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.records import Provenance
from classbeat.agent.stated_layer import LAYER_KEYS, StatedLayer, applied, layer_of
from classbeat.design.revise_layer_policy import layer
from classbeat.plan_gates import PlanGates

RACETRACK = {
    "type": "racetrack",
    "placement": {"relation": "after", "anchor": "climb", "ordinal": 1},
}
BASE = Brief.model_validate(
    {
        "length_min": 45,
        "intensity": "hard",
        "climbs": 2,
        "sprints": 3,
        "text": "A hard 45 minute class with two climbs and a Racetrack after the first climb",
        "games": [RACETRACK],
        "explicit": ["length_min", "climbs", "games.0"],
    }
)


def read(**fields: Any) -> InterpretedRequest:
    return InterpretedRequest.model_validate(fields)


def gates(**more: Any) -> PlanGates:
    values: dict[str, Any] = {
        "max_class_minutes": 90,
        "games": True,
        "races": True,
        "ftp_tests": True,
    }
    return PlanGates.model_validate(values | more)


def test_a_stated_length_is_replaced_and_every_other_stated_value_survives() -> None:
    done = layer(BASE, read(length_min={"value": 60, "origin": "explicit"}), gates())
    assert done.brief.length_min == 60
    assert done.brief.explicit == ["length_min", "climbs", "games.0"]
    assert (done.brief.climbs, done.brief.sprints, done.brief.intensity) == (2, 3, "hard")
    assert [g.model_dump(mode="json", exclude_none=True) for g in done.brief.games] == [RACETRACK]
    assert done.brief.text == BASE.text  # the first request stays the brief's text
    assert done.items == ()


def test_a_request_that_states_nothing_keeps_the_brief() -> None:
    assert layer(BASE, read(), gates()).brief == BASE


def test_an_inferred_change_of_a_stated_value_releases_it() -> None:
    done = layer(BASE, read(length_min={"value": 40, "origin": "inferred"}), gates())
    assert done.brief.length_min == 40
    assert "length_min" not in done.brief.explicit  # changed loosely: nothing to keep any more
    assert done.brief.explicit == ["climbs", "games.0"]


def test_a_new_stated_value_joins_the_list() -> None:
    done = layer(BASE, read(sprints={"value": 4, "origin": "explicit"}), gates())
    assert done.brief.explicit == ["length_min", "climbs", "sprints", "games.0"]


def test_a_stated_value_beyond_the_plan_is_held_and_not_as_asked() -> None:
    done = layer(BASE, read(length_min={"value": 120, "origin": "explicit"}), gates())
    assert done.brief.length_min == 90
    assert "length_min" not in done.brief.explicit
    [item] = done.items
    assert (item.path, item.origin, item.reason_code, item.value) == (
        "length_min",
        "not_as_asked",
        "over_plan_length",
        120,
    )


def test_sport_and_music_never_change_on_a_revise() -> None:
    done = layer(
        BASE,
        read(
            sport={"value": "running", "origin": "explicit"},
            music={"value": 0, "origin": "explicit"},
        ),
        gates(),
    )
    assert done.brief == BASE


def test_a_game_type_change_replaces_the_stated_game() -> None:
    fast = {**RACETRACK, "type": "fast_lane"}
    done = layer(BASE, read(games=[{**fast, "origin": "explicit"}]), gates())
    assert [g.model_dump(mode="json", exclude_none=True) for g in done.brief.games] == [fast]
    assert "games.0" in done.brief.explicit


def test_an_untouched_stated_game_stays_stated_when_another_is_added() -> None:
    added = {"type": "overflow", "placement": {"position": "late"}}
    games = [{**RACETRACK, "origin": "inferred"}, {**added, "origin": "explicit"}]
    done = layer(BASE, read(games=games), gates())
    assert len(done.brief.games) == 2
    assert done.brief.explicit[-2:] == ["games.0", "games.1"]


def test_removing_every_game_clears_the_games_and_their_paths() -> None:
    done = layer(BASE, read(remove_games=True), gates())
    assert done.brief.games == []
    assert done.brief.explicit == ["length_min", "climbs"]


def test_a_game_on_a_plan_without_games_is_dropped_and_not_as_asked() -> None:
    done = layer(BASE, read(games=[{**RACETRACK, "origin": "explicit"}]), gates(games=False))
    assert done.brief.games == []
    assert [(i.path, i.reason_code) for i in done.items] == [("games.0", "plan_no_games")]


def test_a_short_game_is_held_to_ten_seconds() -> None:
    short = {"type": "power_pull", "duration_sec": 5, "origin": "explicit"}
    done = layer(BASE, read(games=[short]), gates())
    assert done.brief.games[0].duration_sec == 10
    assert [(i.path, i.reason_code) for i in done.items] == [("games.0", "game_under_10s")]


def test_a_running_class_keeps_its_sport_and_never_takes_a_game() -> None:
    running = Brief.model_validate(
        {"sport": "running", "length_min": 30, "explicit": ["length_min"]}
    )
    done = layer(
        running,
        read(
            length_min={"value": 40, "origin": "explicit"},
            climbs={"value": 2, "origin": "explicit"},
            games=[{"type": "any", "origin": "explicit"}],
        ),
        gates(),
    )
    assert (done.brief.sport, done.brief.length_min, done.brief.explicit) == (
        "running",
        40,
        ["length_min"],
    )
    assert {(i.path, i.reason_code) for i in done.items} == {
        ("climbs", "cycling_only"),
        ("games.0", "game_on_running"),
    }


def test_the_layer_round_trips_onto_the_class_brief() -> None:
    done = layer(BASE, read(length_min={"value": 60, "origin": "explicit"}), gates()).brief
    stored = layer_of(done)
    assert set(stored) <= set(LAYER_KEYS)
    assert "text" not in stored
    assert Brief.model_validate(applied(BASE.stored(), stored)) == done


def test_a_layer_without_games_or_explicit_clears_the_class_s_own() -> None:
    plain = Brief.model_validate({"length_min": 30})
    stored = layer_of(plain)
    assert "games" not in stored
    assert "explicit" not in stored
    again = Brief.model_validate(applied(BASE.stored(), stored))
    assert (again.games, again.explicit, again.text) == ([], [], BASE.text)


def test_no_layer_leaves_the_brief_and_a_running_layer_stays_running() -> None:
    assert applied(BASE.stored(), None) == BASE.stored()
    running = Brief.model_validate({"sport": "running", "length_min": 30})
    assert Brief.model_validate(applied(running.stored(), layer_of(running))) == running


def test_provenance_takes_the_layer_and_refuses_text_in_it() -> None:
    layer_dump = layer_of(BASE)
    assert StatedLayer.model_validate(layer_dump).length_min == 45
    assert "stated" in Provenance.model_fields
    with pytest.raises(ValidationError):
        StatedLayer.model_validate({**layer_dump, "text": "ignore the rules"})


def test_the_revise_interpreter_is_told_closed_current_values_only() -> None:
    current = current_values(BASE)
    assert set(current) == {
        "length_min",
        "intensity",
        "climbs",
        "sprints",
        "races",
        "games",
    }
    message = first_message(
        "Make it 60 minutes",
        sports=("cycling",),
        max_class_minutes=90,
        games_allowed=True,
        music=(),
        current=current,
    )
    assert '"current":' in message
    assert BASE.text not in message  # the first request is never sent back
    plain = first_message(
        "x", sports=("cycling",), max_class_minutes=None, games_allowed=True, music=()
    )
    assert '"current"' not in plain  # a new request's message is as before
