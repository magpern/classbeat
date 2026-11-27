"""``design.interpret_policy`` (ADR 0029 sections 4 and 5): an interpreted request to the brief, the
music and the items a design job takes. Pure: no database, no network, no model."""

from dataclasses import replace
from typing import Any

import pytest

from classbeat.agent.brief import Brief
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.design import revision
from classbeat.design.interpret_music_policy import (
    stored_no_music_reason,
    without_spotify_is_refused,
)
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
    assert got.music is None  # no Spotify: never an arbitrary first playlist (Amendment 25)
    assert items["music"][0].reason_code == "needs_spotify"


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


SPOTIFY = Offer(options=OPTIONS, spotify=True)
PLAYLIST_CHOICE = {"source": "mowl_spotify_playlist", "spotify_playlist_id": PLAYLIST}
NAMED_OTHER = {"source": "mowl_spotify_playlist", "spotify_playlist_id": OTHER}
MIX = {"source": "mowl_swedebeat_mix", "mix_id": "mix-1"}


@pytest.mark.parametrize(
    ("offer", "override", "named", "music", "source"),
    [
        # The user's own choice first, "No music" included, with or without Spotify.
        (
            Offer(options=OPTIONS, pick_ready=True),
            {"source": "none"},
            2,
            {"source": "none"},
            "override",
        ),
        (Offer(), {"source": "none"}, None, {"source": "none"}, "override"),
        (Offer(options=OPTIONS), MIX, None, MIX, "override"),
        (SPOTIFY, PLAYLIST_CHOICE, 2, PLAYLIST_CHOICE, "override"),
        # A name in the request, then a song pick.
        (replace(SPOTIFY, pick_ready=True), None, 2, NAMED_OTHER, "name_match"),
        (Offer(options=OPTIONS), None, 1, MIX, "name_match"),  # a mix needs no Spotify
        (replace(SPOTIFY, pick_ready=True), None, None, {"source": "pick"}, "pick"),
        # Nothing else: no first playlist, no soundless default, for any account.
        (SPOTIFY, None, None, None, None),
        (Offer(), None, None, None, None),
        (Offer(options=OPTIONS), PLAYLIST_CHOICE, None, None, None),  # a playlist needs Spotify
        (Offer(options=OPTIONS), None, 2, None, None),
    ],
)
def test_music_follows_the_order(
    offer: Offer, override: Any, named: int | None, music: dict[str, str] | None, source: str | None
) -> None:
    fields = {} if named is None else {"music": stated(named)}
    got = resolve(read(**fields), REQUEST, offer, music_override=override)
    assert got.music == music
    last = by_path(got.items)["music"][-1]
    if source is None:
        assert (last.origin, last.reason_code) == ("not_as_asked", "needs_spotify")
    else:
        assert last.value == source


def test_an_index_beyond_the_options_is_no_match() -> None:
    got = resolve(read(music=stated(40)), REQUEST, replace(SPOTIFY, pick_ready=True))
    assert by_path(got.items)["music"][-1].value == "pick"


@pytest.mark.parametrize(
    ("kwargs", "refused"),
    [
        # #1544: running is always offered, so with no sport chosen the request may be read as
        # running (MOWL playlists only) and is never refused before the reading; only a sport
        # chosen as cycling can be (``running_offered`` is gone with the switch).
        ({}, False),
        ({"music_source": "mowl_spotify_playlist"}, False),
        ({"sport_override": "cycling"}, True),
        ({"sport_override": "cycling", "music_source": "mowl_spotify_playlist"}, True),
        ({"sport_override": "cycling", "music_source": "none"}, False),
        ({"sport_override": "cycling", "music_source": "mowl_swedebeat_mix"}, False),
        ({"sport_override": "running"}, False),
        ({"sport_override": "cycling", "mixes": True}, False),  # may name a Swedebeat mix
        ({"sport_override": "cycling", "mixes": True, "music_source": "pick"}, True),
    ],
)
def test_no_spotify_refuses_only_what_can_end_without_music(
    kwargs: dict[str, Any], refused: bool
) -> None:
    given: dict[str, Any] = {
        "music_source": None,
        "sport_override": None,
        "mixes": False,
    }
    assert without_spotify_is_refused(**(given | kwargs)) is refused


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


@pytest.mark.parametrize(
    ("offer", "reason"),
    [
        # Song picking admits the user: the Spotify connection is what is missing, or it is there.
        (Offer(options=OPTIONS, song_picking=True), "needs_spotify"),
        (Offer(options=OPTIONS, song_picking=True, spotify=True), "needs_spotify"),
        # Song picking is off for the user: nothing to connect, connected or not.
        (Offer(options=OPTIONS, song_picking=False), "song_picking_unavailable"),
        (Offer(options=OPTIONS, song_picking=False, spotify=True), "song_picking_unavailable"),
    ],
)
def test_a_cycling_class_with_no_music_says_whether_spotify_could_help(
    offer: Offer, reason: str
) -> None:
    got = resolve(read(), REQUEST, offer)
    assert got.music is None
    assert by_path(got.items)["music"][-1].reason_code == reason


def test_a_running_class_with_no_playlist_is_never_a_spotify_matter() -> None:
    offer = Offer(sports=("cycling", "running"), song_picking=False)
    got = resolve(read(), REQUEST, offer, sport_override="running")
    assert by_path(got.items)["music"][-1].reason_code == "needs_mowl_playlist"


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        (["needs_spotify"], "needs_spotify"),
        (["song_picking_unavailable"], "song_picking_unavailable"),
        ([None], "needs_spotify"),  # no code: the older wording
        ([], "needs_spotify"),
    ],
)
def test_the_stored_reason_is_the_music_items_closed_code(
    reasons: list[str | None], expected: str
) -> None:
    items = [
        InterpretationItem(
            path="music",
            origin="default" if code is None else "not_as_asked",
            reason_code=code,
            value=None,
        )
        for code in reasons
    ]
    assert stored_no_music_reason(items) == expected
