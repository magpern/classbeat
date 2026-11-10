"""``design.interpret_policy`` (ADR 0029 sections 4 and 5): an interpreted request to the brief, the
music and the items a design job takes. Pure: no database, no network, no model."""

from typing import Any

import pytest

from classbeat.agent.brief import Brief
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.design import revision
from classbeat.design.interpret_policy import (
    DEFAULT_LENGTH_MIN,
    MusicOption,
    Offer,
    resolve,
    title_for,
    worst_case_brief,
)
from classbeat.jobs.inputs import jsonb_size
from classbeat.plan_gates import PlanGates

REQUEST = "A 60 minute climb class with three climbs and a game after climb 2"
PLAYLIST = "0123456789abcdefABCDEF"
OTHER = "ZZZZZZZZZZabcdefABCDEF"
OPTIONS = (
    MusicOption("playlist", "Tuesday hits", PLAYLIST),
    MusicOption("mix", "Friday mix", "mix-1"),
    MusicOption("playlist", "Other", OTHER),
)
GATES = PlanGates(max_class_minutes=45, games=True, races=False, ftp_tests=False)


def read(**fields: Any) -> InterpretedRequest:
    return InterpretedRequest.model_validate(fields)


def stated(value: Any, origin: str = "explicit", quote: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value, "origin": origin}
    if quote is not None:
        out["quote"] = quote
    return out


def by_path(items: list[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for item in items:
        out.setdefault(item.path, []).append(item)
    return out


def test_explicit_values_are_kept_and_stated_and_defaults_are_assumed() -> None:
    got = resolve(
        read(length_min=stated(60, quote="60 minute"), climbs=stated(3)),
        REQUEST,
        Offer(options=OPTIONS),
    )
    assert (got.brief.length_min, got.brief.climbs) == (60, 3)
    assert got.brief.explicit == ["length_min", "climbs"]
    assert got.brief.text == REQUEST
    items = by_path(got.items)
    assert items["length_min"][0].origin == "explicit"
    assert items["length_min"][0].quote == "60 minute"
    assert items["intensity"][0].origin == "default"
    assert items["sport"][0].origin == "default"
    assert got.music == {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST}
    assert items["music"][0].value == "first_playlist"


def test_a_quote_that_is_not_in_the_request_is_dropped() -> None:
    got = resolve(read(climbs=stated(3, quote="made up")), REQUEST, Offer())
    assert by_path(got.items)["climbs"][0].quote is None


def test_no_length_takes_the_default_held_to_the_plan() -> None:
    assert resolve(read(), REQUEST, Offer()).brief.length_min == DEFAULT_LENGTH_MIN
    short = PlanGates(max_class_minutes=30, games=True, races=True, ftp_tests=True)
    assert resolve(read(), REQUEST, Offer(gates=short)).brief.length_min == 30


def test_an_explicit_length_over_the_plan_is_not_as_asked_and_held_to_the_cap() -> None:
    got = resolve(read(length_min=stated(90)), REQUEST, Offer(gates=GATES))
    assert got.brief.length_min == 45
    assert "length_min" not in got.brief.explicit
    item = by_path(got.items)["length_min"][0]
    assert (item.origin, item.reason_code, item.value) == ("not_as_asked", "over_plan_length", 90)


def test_an_inferred_length_over_the_plan_is_held_quietly() -> None:
    got = resolve(read(length_min=stated(90, "inferred")), REQUEST, Offer(gates=GATES))
    item = by_path(got.items)["length_min"][0]
    assert (got.brief.length_min, item.origin, item.value) == (45, "inferred", 45)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [("races", 2, "plan_no_races"), ("ftp_test", 20, "plan_no_ftp_tests")],
)
def test_a_plan_feature_it_lacks_is_left_out(field: str, value: int, reason: str) -> None:
    got = resolve(read(**{field: stated(value)}), REQUEST, Offer(gates=GATES))
    assert field not in got.brief.model_fields_set
    assert by_path(got.items)[field][0].reason_code == reason


def test_intensity_and_class_type_are_kept_but_assumed() -> None:
    got = resolve(read(intensity=stated("hard"), class_type=stated("hiit")), REQUEST, Offer())
    assert got.brief.explicit == ["intensity", "class_type"]
    items = by_path(got.items)
    assert items["intensity"][0].origin == "inferred"
    assert items["class_type"][0].section() == "assumed"


def test_a_sport_not_offered_falls_back_to_cycling() -> None:
    got = resolve(read(sport=stated("running")), REQUEST, Offer(sports=("cycling",)))
    assert got.brief.sport == "cycling"
    item = by_path(got.items)["sport"][0]
    assert (item.origin, item.reason_code) == ("not_as_asked", "sport_not_offered")


def test_the_sport_override_is_stated() -> None:
    offer = Offer(sports=("cycling", "running"), options=OPTIONS)
    got = resolve(
        read(sport=stated("cycling", "inferred")), REQUEST, offer, sport_override="running"
    )
    assert got.brief.sport == "running"
    assert "sport" in got.brief.explicit


def test_a_running_class_drops_bike_knobs_and_games_and_uses_a_playlist() -> None:
    offer = Offer(sports=("cycling", "running"), options=OPTIONS, pick_ready=True)
    got = resolve(
        read(
            sport=stated("running"),
            climbs=stated(3),
            games=[{"type": "any", "origin": "explicit"}],
            music=stated(1),  # the mix
        ),
        REQUEST,
        offer,
    )
    brief = got.brief
    assert brief.sport == "running"
    assert not {"climbs", "games"} & brief.model_fields_set
    items = by_path(got.items)
    assert items["climbs"][0].reason_code == "cycling_only"
    assert items["games.0"][0].reason_code == "game_on_running"
    assert [i.reason_code for i in items["music"]] == ["needs_mowl_playlist", None]
    assert got.music == {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST}


def test_a_running_class_with_no_playlist_has_no_music() -> None:
    offer = Offer(sports=("cycling", "running"), options=(OPTIONS[1],))
    got = resolve(read(sport=stated("running")), REQUEST, offer)
    assert got.music is None
    assert by_path(got.items)["music"][-1].reason_code == "needs_mowl_playlist"


def test_games_are_kept_closed_and_a_short_one_is_held_to_the_floor() -> None:
    games = [
        {
            "type": "any",
            "origin": "explicit",
            "placement": {"relation": "after", "anchor": "climb", "ordinal": 2},
        },
        {"type": "any", "origin": "explicit", "duration_sec": 5},
        {"type": "any", "origin": "inferred"},
    ]
    got = resolve(read(games=games), REQUEST, Offer())
    assert len(got.brief.games) == 3
    assert got.brief.games[1].duration_sec == 10
    assert got.brief.explicit == ["games.0", "games.1"]
    items = by_path(got.items)
    assert items["games.1"][0].reason_code == "game_under_10s"
    assert items["games.1"][0].value == {"type": "any", "duration_sec": 5}
    assert items["games.2"][0].origin == "inferred"


def test_a_plan_without_games_drops_every_game() -> None:
    no_games = PlanGates(max_class_minutes=60, games=False, races=True, ftp_tests=True)
    games = [{"type": "any", "origin": "explicit"}, {"type": "any", "origin": "inferred"}]
    got = resolve(read(games=games), REQUEST, Offer(gates=no_games))
    assert got.brief.games == []
    assert [i.reason_code for i in got.items if i.path.startswith("games.")] == ["plan_no_games"]


@pytest.mark.parametrize(
    ("offer", "override", "named", "music", "source"),
    [
        (
            Offer(options=OPTIONS, pick_ready=True),
            {"source": "none"},
            2,
            {"source": "none"},
            "override",
        ),
        (
            Offer(options=OPTIONS, pick_ready=True),
            None,
            2,
            {"source": "mowl_spotify_playlist", "spotify_playlist_id": OTHER},
            "name_match",
        ),
        (
            Offer(options=OPTIONS),
            None,
            1,
            {"source": "mowl_swedebeat_mix", "mix_id": "mix-1"},
            "name_match",
        ),
        (Offer(options=OPTIONS, pick_ready=True), None, None, {"source": "pick"}, "pick"),
        (Offer(), None, None, {"source": "none"}, "soundless"),
    ],
)
def test_music_follows_the_order(
    offer: Offer, override: Any, named: int | None, music: dict[str, str], source: str
) -> None:
    fields = {} if named is None else {"music": stated(named)}
    got = resolve(read(**fields), REQUEST, offer, music_override=override)
    assert got.music == music
    assert by_path(got.items)["music"][-1].value == source


def test_an_index_beyond_the_options_is_no_match() -> None:
    got = resolve(read(music=stated(40)), REQUEST, Offer(options=OPTIONS))
    assert by_path(got.items)["music"][-1].value == "first_playlist"


def test_every_resolved_brief_is_a_valid_brief() -> None:
    got = resolve(read(), REQUEST, Offer())
    assert Brief.model_validate(got.brief.stored()) == got.brief


def test_the_worst_case_of_a_long_ascii_request_fits_the_class() -> None:
    worst = worst_case_brief("a" * 2000)
    assert jsonb_size(worst.stored()) <= revision.MAX_BRIEF_BYTES
    assert jsonb_size(worst_case_brief("å" * 2000).stored()) > revision.MAX_BRIEF_BYTES


def test_the_title_is_the_requests_first_words() -> None:
    assert title_for("  Hills\tand  hits ") == "Hills and hits"
    long = title_for("word " * 40)
    assert len(long) <= 60
    assert long.endswith("…")
    assert not long.endswith(" …")
