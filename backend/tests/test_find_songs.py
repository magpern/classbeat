"""``find_songs`` (issue #1133): songs the model names are searched on Spotify one by one and kept
only when Spotify has that exact song; ``song_match_policy`` is the rule. A fake searcher only: no
Spotify, no network."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.brief import Brief
from classbeat.agent.dispatcher import dispatch, dispatch_async
from classbeat.agent.find_songs import FindSongsInput, WantedSong, song_query
from classbeat.agent.music_taste import EraRange, MusicTaste
from classbeat.agent.pick_pool import PoolContext, SearchUnavailable, pool_stage
from classbeat.agent.provider import ToolCall
from classbeat.agent.registry import SearchRegistry
from classbeat.agent.song_match_policy import is_match, title_core
from classbeat.agent.songs import PickPlan
from classbeat.agent.stage import Stage

# --- the match rule -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("artist", "title", "hit_title", "hit_artists", "year", "years", "ok"),
    [
        ("Roxette", "Joyride", "Joyride", ["Roxette"], 1991, None, True),
        ("roxette", "JOYRIDE!", "Joyride", ["Roxette"], 1991, None, True),  # case, punctuation
        ("Roxette", "Joyride", "Joyride - Remastered 2009", ["Roxette"], 2009, None, True),
        ("Roxette", "Joyride", "Joyride (Single Version)", ["Roxette"], 1991, None, True),
        (
            "Europe",
            "The Final Countdown",
            "The Final Countdown",
            ["Europe"],
            1986,
            (1980, 1989),
            True,
        ),
        ("Avicii", "Levels", "Levels", ["Avicii", "Etta James"], 2011, None, True),  # one artist
        ("Lisa Ekdahl", "Vem vet", "Vem vet", ["Lisa Ekdahl"], 1994, (1990, 1999), True),
        # Not the song: another artist, a part of the name, another title.
        ("Roxette", "Joyride", "Joyride", ["Roxette Tribute Band"], 1991, None, False),
        ("Rox", "Joyride", "Joyride", ["Roxette"], 1991, None, False),
        ("Roxette", "Joyride", "The Look", ["Roxette"], 1989, None, False),
        ("Roxette", "Joy", "Joyride", ["Roxette"], 1991, None, False),
        # Another version the request did not ask for.
        ("Roxette", "Joyride", "Joyride - Live", ["Roxette"], 1991, None, False),
        ("Roxette", "Joyride", "Joyride (Karaoke Version)", ["Roxette"], 1991, None, False),
        ("Roxette", "Joyride", "Joyride - Acoustic", ["Roxette"], 1991, None, False),
        ("Roxette", "Joyride (Live)", "Joyride (Live)", ["Roxette"], 1991, None, True),  # asked
        # Outside the era, or no year when one is asked for.
        ("Roxette", "Joyride", "Joyride", ["Roxette"], 2011, (1990, 1999), False),
        ("Roxette", "Joyride", "Joyride", ["Roxette"], None, (1990, 1999), False),
        ("", "Joyride", "Joyride", ["Roxette"], 1991, None, False),
    ],
)
def test_a_hit_is_the_song_only_when_artist_title_version_and_era_agree(
    artist: str,
    title: str,
    hit_title: str,
    hit_artists: list[str],
    year: int | None,
    years: tuple[int, int] | None,
    ok: bool,
) -> None:
    assert is_match(artist, title, hit_title, hit_artists, year, years) is ok


@pytest.mark.parametrize(
    ("title", "core"),
    [
        ("Joyride", "Joyride"),
        ("Joyride - Remastered 2009", "Joyride"),
        ("Joyride (Single Version) [2009 Remaster]", "Joyride"),
        ("(I Just) Died in Your Arms", "(I Just) Died in Your Arms"),
        ("- Remix", "- Remix"),  # nothing would be left: kept as it is
    ],
)
def test_the_title_core_sets_version_tails_aside(title: str, core: str) -> None:
    assert title_core(title) == core


# --- the tool --------------------------------------------------------------------------------

ID = "{:0>22}"


def hit(n: int, title: str, artists: Sequence[str], year: int, sec: int = 230) -> dict[str, Any]:
    return {
        "id": ID.format(n),
        "title": title,
        "artists": list(artists),
        "album": "Album",
        "year": str(year),
        "sec": sec,
    }


CATALOGUE = {
    'track:"Joyride" artist:"Roxette" year:1990-1999': [
        hit(1, "Joyride - Live", ["Roxette"], 1991),
        hit(2, "Joyride", ["Roxette"], 1991),
    ],
    'track:"The Look" artist:"Roxette" year:1990-1999': [],  # 1989: not in the 90s search
    'track:"Made Up Song" artist:"Roxette" year:1990-1999': [hit(3, "Joyride", ["Roxette"], 1991)],
}


class Searches:
    """A fake ``Searcher``: answers from ``CATALOGUE`` and records every query it was asked."""

    def __init__(self, *, fail: bool = False, slow: bool = False) -> None:
        self.queries: list[str] = []
        self.fail = fail
        self.slow = slow

    def __call__(self, query: str) -> Any:
        self.queries.append(query)
        if self.fail:
            raise SearchUnavailable
        found = CATALOGUE.get(query, [])
        if not self.slow:
            return found

        async def later() -> Any:
            await asyncio.sleep(0)
            return found

        return later()


def context(searcher: Searches, taste: MusicTaste | None = None) -> PoolContext:
    brief = Brief(length_min=30, music_taste=taste)
    return PoolContext(PickPlan.build(brief), SearchRegistry(), searcher)


NINETIES = {"year_from": 1990, "year_to": 1999}
SONGS = {
    "songs": [
        {"artist": "Roxette", "track": "Joyride"},
        {"artist": "Roxette", "track": "The Look"},
        {"artist": "Roxette", "track": "Made Up Song"},
    ],
    **NINETIES,
}


def run(stage: Stage, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return json.loads(dispatch(stage, ToolCall("c1", name, args)).result.content)


def test_each_song_is_searched_by_artist_title_and_the_eras_years() -> None:
    searches = Searches()
    stage = pool_stage(context(searches))
    answer = run(stage, "find_songs", SONGS)
    assert searches.queries == list(CATALOGUE)
    # Only the song Spotify has is found, as the original release; the made-up song's hit is
    # another song, and the 1989 song is not in a 90s search: both missing.
    assert [(f["song"], f["title"], f["year"]) for f in answer["found"]] == [(0, "Joyride", 1991)]
    assert answer["missing"] == [1, 2]
    assert answer["candidates_found"] == 1
    assert "id" not in answer["found"][0]


def test_a_missing_song_has_no_ref_and_cannot_enter_the_pool() -> None:
    ctx = context(Searches())
    stage = pool_stage(ctx)
    run(stage, "find_songs", SONGS)
    assert len(ctx.registry) == 1  # the live version and the other song were never registered
    assert ctx.registry.spotify_id(0) == ID.format(2)
    refused = run(stage, "check_pool", {"entries": [{"ref": 1, "role": "warmup"}]})
    assert refused["error"] == "out_of_range"


def test_an_async_search_is_awaited_in_order() -> None:
    searches = Searches(slow=True)
    stage = pool_stage(context(searches))
    done = asyncio.run(dispatch_async(stage, ToolCall("c1", "find_songs", SONGS)))
    assert json.loads(done.result.content)["missing"] == [1, 2]
    assert searches.queries == list(CATALOGUE)


def test_a_search_that_cannot_run_is_the_fixed_sentence() -> None:
    stage = pool_stage(context(Searches(fail=True)))
    done = run(stage, "find_songs", SONGS)
    assert done["error"] == "upstream_unavailable"
    assert done["messages"] == ["search is not available right now"]


@pytest.mark.parametrize(
    ("song", "years", "query"),
    [
        ({"artist": "Roxette", "track": "Joyride"}, None, 'track:"Joyride" artist:"Roxette"'),
        (
            {"artist": 'Rox" year:2020', "track": "Joy:ride"},
            (1990, 1999),
            'track:"Joy ride" artist:"Rox year 2020" year:1990-1999',
        ),
        ({"artist": "A", "track": "B"}, (1984, 1984), 'track:"B" artist:"A" year:1984'),
    ],
)
def test_the_query_is_the_backends_and_the_words_cannot_become_syntax(
    song: dict[str, str], years: tuple[int, int] | None, query: str
) -> None:
    assert song_query(WantedSong.model_validate(song), years) == query


@pytest.mark.parametrize(
    "bad",
    [
        {"songs": []},
        {"songs": [{"artist": "A", "track": "B"}] * 11},
        {"songs": [{"artist": " : ", "track": "B"}]},
        {"songs": [{"artist": "A", "track": "B", "spotify_id": ID.format(1)}]},
        {"songs": [{"artist": "A", "track": "B"}], "year_from": 2000, "year_to": 1990},
        {"songs": [{"artist": "A", "track": "B"}], "market": "SE"},
    ],
)
def test_the_input_is_songs_and_years_only(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FindSongsInput.model_validate(bad)


def test_a_valid_pool_counts_its_candidates_per_stated_era_as_a_note() -> None:
    taste = MusicTaste(
        eras=[EraRange(year_from=1990, year_to=1999), EraRange(year_from=2000, year_to=2009)]
    )
    ctx = context(Searches(), taste)
    ctx.registry.add_hits(
        [
            hit(10, "A", ["X"], 1991),
            hit(11, "B", ["X"], 1995),
            hit(12, "C", ["X"], 2004),
            hit(13, "D", ["X"], 2015),
            hit(14, "E", ["X"], 1992),
            hit(15, "F", ["X"], 1993),
            hit(16, "G", ["X"], 1994),
            hit(17, "H", ["X"], 1996),
        ]
    )
    roles = ["warmup", "build", "interval", "recovery", "interval", "build", "cooldown", "cooldown"]
    entries = [{"ref": n, "role": r} for n, r in enumerate(roles)]
    done = run(pool_stage(ctx), "check_pool", {"entries": entries})
    assert done["valid"] is True
    assert (done["by_era"], done["outside_eras"]) == ([6, 1], 1)
    plain = context(Searches())
    plain.registry.add_hits([hit(10 + n, chr(65 + n), ["X"], 1991) for n in range(8)])
    without = run(pool_stage(plain), "check_pool", {"entries": entries})
    assert "by_era" not in without
