"""Album cover URLs (ADR 0013 section 9, issue #251): the pinned host check, where a cover is
kept and how ``song_list`` content ``v: 2`` carries it.

No image is ever requested: the URLs are strings in fakes."""

import json
from typing import Any

import pytest

from classbeat.agent.choose_songs import ChosenList, ChosenSong
from classbeat.agent.registry import SearchRegistry
from classbeat.agent.songs import KeptSong, PoolTrack, kept_view, pool_view
from classbeat.covers import cover_url
from classbeat.songs import song_list
from classbeat.songs.song_list import SongListContent

HEX = "ab12cd34ef" * 4  # 40 lowercase hex digits
GOOD = f"https://i.scdn.co/image/{HEX}"
OTHER = f"https://i.scdn.co/image/{'0' * 40}"

DROPPED = [
    pytest.param(f"https://mosaic.scdn.co/image/{HEX}", id="mosaic-host"),
    pytest.param(f"https://evil.example/image/{HEX}", id="other-host"),
    pytest.param(f"https://i.scdn.co.evil.example/image/{HEX}", id="host-suffix"),
    pytest.param(f"https://evil.example/https://i.scdn.co/image/{HEX}", id="host-in-path"),
    pytest.param(f"http://i.scdn.co/image/{HEX}", id="http"),
    pytest.param(f"//i.scdn.co/image/{HEX}", id="scheme-relative"),
    pytest.param(f"https://i.scdn.co/image/{HEX.upper()}", id="uppercase-hex"),
    pytest.param(f"https://I.SCDN.CO/image/{HEX}", id="uppercase-host"),
    pytest.param(f"https://i.scdn.co/image/{HEX[:39]}", id="39-hex"),
    pytest.param(f"https://i.scdn.co/image/{HEX}a", id="41-hex"),
    pytest.param(f"{GOOD}\n", id="trailing-newline"),
    pytest.param(f"{GOOD}?size=640", id="query"),
    pytest.param(f"{GOOD}#x", id="fragment"),
    pytest.param(f" {GOOD}", id="leading-space"),
    pytest.param(f"https://i.scdn.co/image/{HEX[:39]}g", id="not-hex"),
    pytest.param(f"https://i.scdn.co:443/image/{HEX}", id="port"),
    pytest.param(f"https://user@i.scdn.co/image/{HEX}", id="userinfo"),
    pytest.param(f"https://i.scdn.co/images/{HEX}", id="other-path"),
    pytest.param(f"javascript:alert(1)//{GOOD}", id="javascript"),
    pytest.param(f"data:image/png;base64,{HEX}", id="data"),
    pytest.param("", id="empty"),
    pytest.param(None, id="none"),
    pytest.param(42, id="number"),
    pytest.param([GOOD], id="list"),
    pytest.param({"url": GOOD}, id="object"),
    pytest.param(GOOD.encode(), id="bytes"),
]


def test_a_cover_on_the_pinned_host_is_kept() -> None:
    assert cover_url(GOOD) == GOOD
    assert cover_url(OTHER) == OTHER


@pytest.mark.parametrize("value", DROPPED)
def test_anything_else_is_dropped(value: Any) -> None:
    assert cover_url(value) is None


def test_a_str_subclass_is_dropped() -> None:
    class Sneaky(str):
        pass

    assert cover_url(Sneaky(GOOD)) is None


# --- the search registry: the cover is the backend's, never in what the model is shown --------


def hit(n: int, image: object) -> dict[str, Any]:
    return {
        "id": f"CoverTrack{n:012d}",
        "title": f"Song {n}",
        "artists": ["A Band"],
        "album": "An Album",
        "year": "2001",
        "sec": 200,
        "image": image,
    }


def test_the_registry_keeps_a_valid_cover_and_never_shows_it() -> None:
    reg = SearchRegistry()
    entries = reg.add_hits([hit(0, GOOD), hit(1, f"{GOOD}\n"), hit(2, None)])
    assert [e.cover for e in entries] == [GOOD, None, None]
    shown = json.dumps([e.shown() for e in entries])
    assert "scdn" not in shown
    assert "cover" not in shown
    assert "scdn" not in repr(reg) + repr(entries)


def test_the_model_views_of_pool_and_kept_songs_have_no_cover() -> None:
    track = PoolTrack("CoverTrack000000000000", "T", "A", "build", 120.0, 200, cover=GOOD)
    kept = KeptSong(0, "CoverTrack000000000001", "T", "A", "warmup", 100.0, 200, cover=GOOD)
    views = json.dumps([pool_view(track, 0), kept_view(kept, 0)])
    assert "scdn" not in views
    assert "cover" not in views
    assert "scdn" not in repr(track) + repr(kept)


# --- song_list content v: 2 ---------------------------------------------------------------------


def chosen(*covers: str | None) -> ChosenList:
    songs = tuple(
        ChosenSong(f"CoverTrack{n:012d}", f"Song {n}", "A Band", role, 120.0, 300, True, cover)
        for n, (role, cover) in enumerate(zip(("warmup", "build", "cooldown"), covers, strict=True))
    )
    return ChosenList(songs, 882, 900, 9.0, "pick", 0, "")


def built(*covers: str | None) -> song_list.SongListRevision:
    return song_list.build(chosen(*covers), account="spotify-user", provenance={})


def test_a_valid_cover_is_stored_in_v2_and_anything_else_as_null() -> None:
    made = built(GOOD, f"https://evil.example/image/{HEX}", None)
    dumped = made.content.model_dump(mode="json")
    assert dumped["v"] == 2
    assert [s["cover"] for s in dumped["songs"]] == [GOOD, None, None]
    assert made.content_hash == song_list.content_hash(dumped)


@pytest.mark.parametrize("value", DROPPED[:20])
def test_every_dropped_url_is_null_in_the_stored_list(value: Any) -> None:
    made = built(value, GOOD, None)
    assert [s.cover for s in made.content.songs] == [None, GOOD, None]


def test_the_hash_covers_the_cover() -> None:
    a, b, none = built(GOOD, None, None), built(OTHER, None, None), built(None, None, None)
    assert len({a.content_hash, b.content_hash, none.content_hash}) == 3
    assert a.track_ids == b.track_ids == none.track_ids  # only the cover differs


def v1_content() -> dict[str, Any]:
    dumped = built(GOOD, None, None).content.model_dump(mode="json")
    dumped["v"] = 1
    for s in dumped["songs"]:
        del s["cover"]
    return dumped


def test_v1_content_still_parses_and_reads_as_no_cover() -> None:
    content = SongListContent.model_validate(v1_content())
    assert content.v == 1
    assert [s.cover for s in content.songs] == [None, None, None]


def test_v1_content_with_a_cover_is_refused() -> None:
    raw = v1_content()
    raw["songs"][0]["cover"] = GOOD
    with pytest.raises(ValueError, match="v: 2"):
        SongListContent.model_validate(raw)


def test_a_stored_cover_that_no_longer_passes_reads_as_null() -> None:
    """The check runs again whenever content is parsed (``load``), before anything is returned."""
    raw = built(GOOD, GOOD, None).content.model_dump(mode="json")
    raw["songs"][0]["cover"] = f"https://evil.example/image/{HEX}"
    raw["songs"][1]["cover"] = f"{GOOD}?x=1"
    content = SongListContent.model_validate(raw)
    assert [s.cover for s in content.songs] == [None, None, None]


def test_another_version_is_refused() -> None:
    raw = v1_content()
    raw["v"] = 3
    with pytest.raises(ValueError, match="literal_error"):
        SongListContent.model_validate(raw)
