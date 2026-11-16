"""The check after the choice (issue #1133, ``agent.taste_check_policy``): a song list that misses a
stated era or is shorter than the class carries Not as asked items in its content, instead of
being stored as if it kept to the request. Pure: no database, no network."""

from dataclasses import dataclass
from typing import Any

import pytest

from classbeat.agent import songs
from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.agent.music_taste import EraRange, MusicTaste
from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.taste_check_policy import (
    ERA_TOO_FEW,
    LENGTH_TOLERANCE,
    OUTSIDE_ERAS,
    TOO_SHORT,
    list_unmet,
    unmet,
)
from classbeat.songs import song_list

EIGHTIES = EraRange(year_from=1980, year_to=1989)
NINETIES = EraRange(year_from=1990, year_to=1999)
NOUGHTIES = EraRange(year_from=2000, year_to=2009)
THREE = MusicTaste(eras=[EIGHTIES, NINETIES, NOUGHTIES], language="sv", hits=True)
ID = "{:0>22}"


@dataclass(frozen=True)
class Song:
    spotify_id: str
    new: bool = True


def picks(years: list[int | None]) -> list[PoolPick]:
    return [PoolPick(n, "build", ID.format(n), None, y) for n, y in enumerate(years)]


def codes(found: list[Any]) -> list[tuple[str, str | None]]:
    return [(i.path, i.reason_code) for i in found]


def check(taste: MusicTaste | None, years: list[int | None], **kw: Any) -> list[Any]:
    args: dict[str, Any] = {"mode": "pick", "total_sec": 2700, "target_sec": 2700, **kw}
    chosen = [Song(ID.format(n)) for n in range(len(years))]
    return unmet(taste, picks(years), chosen, **args)


@pytest.mark.parametrize(
    ("taste", "years", "expected"),
    [
        # Every era has its share (12 songs over 3 eras: at least 2 each).
        (THREE, [1981, 1985, 1987, 1989, 1991, 1993, 1995, 1999, 2001, 2003, 2005, 2009], []),
        (THREE, [1981, 1985, 1991, 1993, 2001, 2003, 1982, 1983, 1984, 1986, 1988, 1980], []),
        # The live miss: no song from the 00s, and three songs from other years.
        (
            THREE,
            [1981, 1985, 1987, 1989, 1991, 1993, 1995, 1999, 2012, 2015, 2018, 1984],
            [("music_era.2", ERA_TOO_FEW), ("music_taste", OUTSIDE_ERAS)],
        ),
        # One song from an era is too few when the even share is two.
        (
            THREE,
            [1981, 1985, 1987, 1989, 1991, 1993, 1995, 1999, 2001, 1982, 1983, 1984],
            [("music_era.2", ERA_TOO_FEW)],
        ),
        # A song with no known year is from no era.
        (MusicTaste(eras=[EIGHTIES]), [1981, None], [("music_taste", OUTSIDE_ERAS)]),
        # One era and few songs: at least one from it.
        (
            MusicTaste(eras=[EIGHTIES]),
            [1999],
            [("music_era.0", ERA_TOO_FEW), ("music_taste", OUTSIDE_ERAS)],
        ),
        # No eras: nothing to judge (a language or style words cannot be checked).
        (MusicTaste(language="sv", genres=["disco"], hits=True), [2015, 2018], []),
        (None, [2015, 2018], []),
    ],
)
def test_the_eras_are_checked_against_the_chosen_songs_years(
    taste: MusicTaste | None, years: list[int | None], expected: list[tuple[str, str]]
) -> None:
    assert codes(check(taste, years)) == expected


def test_an_era_miss_says_which_era_and_how_many_were_found() -> None:
    (miss, outside) = check(MusicTaste(eras=[EIGHTIES]), [1999, 2001, 2003])
    assert miss.origin == "not_as_asked"
    assert miss.value == {"from": 1980, "to": 1989, "found": 0}
    assert outside.value == {"songs": 3}


def test_a_replacement_is_judged_on_its_length_only() -> None:
    assert check(THREE, [2015, 2018], mode="replace") == []
    short = check(THREE, [2015], mode="replace", total_sec=900)
    assert codes(short) == [("music_length", TOO_SHORT)]


@pytest.mark.parametrize(
    ("total", "target", "short"),
    [(900, 2700, True), (2429, 2700, True), (2430, 2700, False), (2700, 2700, False)],
)
def test_a_list_shorter_than_the_class_is_not_as_asked(
    total: int, target: int, short: bool
) -> None:
    found = check(None, [], total_sec=total, target_sec=target)
    assert bool(found) is short
    if short:
        assert found[0].value == {"total_min": round(total / 60), "target_min": round(target / 60)}


def test_kept_songs_are_not_judged_by_year() -> None:
    chosen = [Song(ID.format(0)), Song(ID.format(1), new=False)]
    taste = MusicTaste(eras=[EIGHTIES])
    found = unmet(taste, picks([1985, 2015]), chosen, mode="pick", total_sec=10, target_sec=10)
    assert found == []


def test_the_length_tolerance_is_the_choices_own() -> None:
    assert LENGTH_TOLERANCE == songs.TOLERANCE


# --- the stored list ---------------------------------------------------------------------------


def chosen_list(total: int = 2700) -> ChosenList:
    song = ChosenSong(ID.format(0), "Joyride", "Roxette", "warmup", 120.0, 240, True)
    return ChosenList((song,), total, 2700, 9.0, "pick", 0, "")


def test_a_list_that_keeps_to_the_request_is_stored_and_hashed_as_before() -> None:
    built = song_list.build(chosen_list(), account="user", provenance={})
    dumped = built.content.model_dump(mode="json")
    assert "unmet" not in dumped
    assert built.content_hash == song_list.content_hash(dumped)


def test_a_list_that_misses_carries_its_items_and_they_are_hashed() -> None:
    chosen = chosen_list(total=900)
    items = list_unmet(MusicTaste(eras=[EIGHTIES]), picks([2015]), chosen)
    assert codes(items) == [
        ("music_era.0", ERA_TOO_FEW), ("music_taste", OUTSIDE_ERAS), ("music_length", TOO_SHORT),
    ]  # fmt: skip
    built = song_list.build(chosen, account="user", provenance={}, unmet=items)
    dumped = built.content.model_dump(mode="json")
    assert [i["reason_code"] for i in dumped["unmet"]] == [ERA_TOO_FEW, OUTSIDE_ERAS, TOO_SHORT]
    assert built.content_hash == song_list.content_hash(dumped)
    assert song_list.SongListContent.model_validate(dumped) == built.content


def test_unmet_holds_not_as_asked_items_only() -> None:
    dumped = song_list.build(chosen_list(), account="user", provenance={}).content.model_dump()
    asked = {"path": "music_era.0", "origin": "explicit", "value": {"from": 1980, "to": 1989}}
    with pytest.raises(ValueError, match="Not as asked"):
        song_list.SongListContent.model_validate({**dumped, "unmet": [asked]})
