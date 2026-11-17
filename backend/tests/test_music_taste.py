"""The music taste of a request (issue #1133): what the interpreter may return, how it is kept, and
how ``interpret_policy.resolve`` turns it into the brief and the items of "How we read your
request". Pure: no database, no network, no model."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.brief import Brief
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.music_taste import (
    GENRE_CHARS,
    MAX_ERAS,
    EraRange,
    MusicTaste,
    keep_word,
    largest,
    taste_view,
)
from classbeat.agent.song_input import song_facts
from classbeat.design import revision
from classbeat.design.interpret_policy import MusicOption, Offer, resolve, worst_case_brief
from classbeat.design.interpret_taste_policy import TASTE_NEEDS_PICK
from classbeat.jobs.inputs import jsonb_size

LIVE_MISS = "45 minutes, only Swedish hit songs from the 80s, 90s and 00s"
PLAYLIST = "0123456789abcdefABCDEF"
OPTIONS = (MusicOption("playlist", "Tuesday hits", PLAYLIST),)
PICK = Offer(options=OPTIONS, pick_ready=True, spotify=True)


def era(low: int, high: int, origin: str = "explicit", quote: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"year_from": low, "year_to": high, "origin": origin}
    if quote is not None:
        out["quote"] = quote
    return out


def stated(value: Any, origin: str = "explicit", quote: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value, "origin": origin}
    if quote is not None:
        out["quote"] = quote
    return out


def taste(**fields: Any) -> InterpretedRequest:
    return InterpretedRequest.model_validate({"music_taste": fields})


LIVE_READING = taste(
    eras=[era(1980, 1989, quote="80s"), era(1990, 1999, quote="90s"), era(2000, 2009, quote="00s")],
    language=stated("sv", quote="Swedish"),
    hits=stated(True, quote="hit songs"),
)


# --- what the interpreter may return ---------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"eras": [era(1990, 1980)]},  # backwards
        {"eras": [era(1950, 1999)]},  # longer than an era
        {"eras": [era(1899, 1905)]},  # before the first year
        {"eras": [era(1980, 1989)] * (MAX_ERAS + 1)},
        {"language": stated("swedish")},  # not an ISO 639-1 code
        {"language": stated("SV")},
        {"genres": [stated("x" * (GENRE_CHARS + 1))]},
        {"genres": [stated("rock")] * 4},
        {"hits": stated(False)},  # hits are asked for or not reported
        {"eras": [{**era(1980, 1989), "decade": "80s"}]},  # nothing beyond the closed fields
        {"mood": stated("happy")},
    ],
)
def test_the_taste_is_closed(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        taste(**bad)


@pytest.mark.parametrize(
    ("word", "request_text", "kept"),
    [
        ("disco", "Disco and funk from the 70s", "Disco"),  # the request's own spelling
        ("DISCO", "a disco class", "disco"),
        ("synthpop", "a disco class", None),  # not in the request: made up
        ("", "a disco class", None),
        ("x" * (GENRE_CHARS + 1), "x" * 40, None),
        ("schlager", "Straße and schlager", None),  # a fold that changes the length: not trusted
        ("dansband", "svensk dansband från 90-talet", "dansband"),
    ],
)
def test_a_style_word_is_kept_only_as_the_request_writes_it(
    word: str, request_text: str, kept: str | None
) -> None:
    assert keep_word(word, request_text) == kept


def test_sanitised_keeps_verbatim_quotes_and_request_words_only() -> None:
    request = "Swedish disco hits from the 80s"
    read = taste(
        eras=[era(1980, 1989, quote="80s"), era(1990, 1999, quote="the nineties")],
        language=stated("sv", quote="Svenska"),
        genres=[stated("DISCO"), stated("synthpop")],
        hits=stated(True, quote="hits"),
    ).sanitised(request, 0)
    kept = read.music_taste
    assert kept is not None
    assert [e.quote for e in kept.eras] == ["80s", None]
    assert kept.language is not None
    assert kept.language.quote is None
    assert [g.value for g in kept.genres] == ["disco"]
    assert kept.hits is not None
    assert kept.hits.quote == "hits"


# --- the brief and "How we read your request" -----------------------------------------------


def test_the_live_miss_is_read_as_three_eras_swedish_hits() -> None:
    got = resolve(LIVE_READING.sanitised(LIVE_MISS, 1), LIVE_MISS, PICK)
    assert got.music == {"source": "pick"}
    assert got.brief.music_taste == MusicTaste(
        eras=[
            EraRange(year_from=1980, year_to=1989),
            EraRange(year_from=1990, year_to=1999),
            EraRange(year_from=2000, year_to=2009),
        ],
        language="sv",
        hits=True,
    )
    items = {i.path: i for i in got.items if i.path.startswith("music_")}
    assert list(items) == [
        "music_era.0", "music_era.1", "music_era.2", "music_language", "music_hits",
    ]  # fmt: skip
    assert items["music_era.1"].value == {"from": 1990, "to": 1999}
    assert items["music_era.1"].quote == "90s"
    assert items["music_language"].value == "sv"
    assert items["music_hits"].value is True
    assert all(i.origin == "explicit" for i in items.values())


def test_a_style_word_is_an_item_with_the_word_as_its_quote() -> None:
    request = "a disco class"
    got = resolve(taste(genres=[stated("disco", "inferred")]), request, PICK)
    (item,) = [i for i in got.items if i.path == "music_genre.0"]
    assert (item.origin, item.value, item.quote) == ("inferred", None, "disco")
    assert got.brief.music_taste == MusicTaste(genres=["disco"])


@pytest.mark.parametrize(
    ("offer", "music_reading"),
    [
        (Offer(options=OPTIONS, pick_ready=False, spotify=True), {"music": stated(0)}),  # named
        (Offer(options=OPTIONS, pick_ready=False, spotify=False), {}),  # no music at all
        (Offer(sports=("cycling", "running"), options=OPTIONS), {"sport": stated("running")}),
    ],
    ids=["named_playlist", "no_music", "running"],
)
def test_a_taste_without_a_song_pick_is_not_as_asked_when_stated(
    offer: Offer, music_reading: dict[str, Any]
) -> None:
    read = InterpretedRequest.model_validate(
        {
            **music_reading,
            "music_taste": {
                "eras": [era(1980, 1989)],
                "genres": [stated("disco", "inferred")],
            },
        }
    )
    got = resolve(read, "Tuesday hits, disco from the 80s", offer)
    assert got.brief.music_taste is None
    taste_items = [i for i in got.items if i.path.startswith("music_")]
    # The stated era is Not as asked; the inferred style word is left out quietly.
    assert [(i.path, i.origin, i.reason_code) for i in taste_items] == [
        ("music_era.0", "not_as_asked", TASTE_NEEDS_PICK)
    ]


def test_no_taste_leaves_the_brief_and_its_bytes_as_before() -> None:
    got = resolve(InterpretedRequest(), "A 45 minute class", PICK)
    assert got.brief.music_taste is None
    assert "music_taste" not in got.brief.model_dump(mode="json")
    assert not [i for i in got.items if i.path.startswith("music_")]


def test_the_taste_round_trips_through_the_stored_brief() -> None:
    got = resolve(LIVE_READING, LIVE_MISS, PICK)
    stored = json.loads(json.dumps(got.brief.stored()))
    assert Brief.model_validate(stored) == got.brief


def test_the_worst_case_brief_holds_the_largest_taste_and_still_fits() -> None:
    worst = worst_case_brief("a" * 2000)
    assert worst.music_taste == largest()
    assert jsonb_size(worst.stored()) <= revision.MAX_BRIEF_BYTES


# --- what the song stages are told ---------------------------------------------------------


def test_the_song_stages_are_told_the_taste_only_when_there_is_one() -> None:
    def facts(brief: Brief) -> dict[str, Any]:
        return song_facts(
            brief, mode="pick", target_sec=2700, n=11, crossfade_sec=9.0, replace_roles=(), kept=()
        )

    plain = Brief(length_min=45)
    assert "music_taste" not in facts(plain)
    eighties = [EraRange(year_from=1980, year_to=1989)]
    held = MusicTaste(eras=eighties, language="sv", genres=["disco"], hits=True)
    tasted = plain.model_copy(update={"music_taste": held})
    assert facts(tasted)["music_taste"] == {
        "eras": [{"from": 1980, "to": 1989}],
        "language": "sv",
        "genres": ["disco"],
        "hits": True,
    }
    assert taste_view(MusicTaste()) == {}
