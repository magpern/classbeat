"""Spotify's ``explicit`` flag through the song search (ADR 0040 section 6, #1340): the library's
``search_tracks`` drops it, so the job's search reads the same request through the library's own
reducer with the flag kept (``songs.search_hits_calc``), the registry keeps it for the backend
alone, and a model is never shown it.

Offline: the in-process fake Spotify through the worker's allowlisted opener.
"""

import inspect
import json
import urllib.request
from typing import Any

import pytest
from mowl import spotify_web

from classbeat.agent.registry import SearchRegistry
from classbeat.connections import Credentials
from classbeat.songs.search_hits_calc import hits
from spotifycore import FakeSpotify, track_id
from spotifyfake import CALLBACK, CLIENT_A, USER_A, worker_opener

reduce = spotify_web._track  # pyright: ignore[reportPrivateUsage]


def item(n: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": track_id(n),
        "type": "track",
        "name": f"Song {n}",
        "duration_ms": 200_000,
        "artists": [{"name": "A Band"}],
        "album": {"name": "An Album", "release_date": "2001-01-01"},
        **extra,
    }


def answer(*items: Any) -> dict[str, Any]:
    return {"tracks": {"items": list(items)}}


# --- the hits ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ({"explicit": True}, True),
        ({"explicit": False}, False),
        ({}, "absent"),
        ({"explicit": None}, "absent"),
        ({"explicit": "true"}, "absent"),
        ({"explicit": 1}, "absent"),
        ({"explicit": 0}, "absent"),
        ({"explicit": []}, "absent"),
    ],
)
def test_the_flag_is_kept_only_when_spotify_sent_a_boolean(sent: dict[str, Any], kept: Any) -> None:
    found = hits(answer(item(0, **sent)), reduce)
    assert len(found) == 1
    assert found[0].get("explicit", "absent") == kept


def test_a_hit_is_otherwise_exactly_what_the_library_makes() -> None:
    raw = [item(0, explicit=True), item(1), {"type": "episode"}, None, item(2, explicit=False)]
    found = hits(answer(*raw), reduce)
    plain = [h for h in map(reduce, raw) if h]
    assert [{k: v for k, v in h.items() if k != "explicit"} for h in found] == plain
    assert [h.get("explicit") for h in found] == [True, None, False]


@pytest.mark.parametrize(
    "shape", [None, [], "x", {}, {"tracks": None}, {"tracks": {}}, {"tracks": {"items": "x"}}]
)
def test_an_answer_of_another_shape_gives_no_hits(shape: Any) -> None:
    assert hits(shape, reduce) == []


def search(creds: Credentials, query: str) -> list[dict[str, Any]]:
    params = {"q": query, "type": "track", "limit": spotify_web.SEARCH_LIMIT}
    return hits(spotify_web.call(creds, "GET", "search", params), reduce)


def test_it_reads_the_same_request_as_the_librarys_search_and_adds_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSpotify()
    fake.add_user(USER_A)
    fake.add_app(CLIENT_A, redirect_uris=[CALLBACK], allowed=[USER_A])
    access, _ = fake.grant(CLIENT_A, USER_A)
    monkeypatch.setattr(urllib.request, "_opener", worker_opener(fake))
    creds = Credentials(
        spotify_client_id=CLIENT_A,
        spotify_access_token=access,
        spotify_expires_at=1e12,
        spotify_refresh_enabled=False,
        spotify_max_retry_wait=0,
    )
    first = fake.catalogue[track_id(0)]["name"].split()[0]
    library = spotify_web.search_tracks(creds, first)
    assert len(library) >= 2
    assert all("explicit" not in h for h in library)  # what it drops
    fake.explicit[library[0]["id"]] = True
    fake.explicit[library[1]["id"]] = False
    ours = search(creds, first)
    assert [{k: v for k, v in h.items() if k != "explicit"} for h in ours] == library
    flags = [h.get("explicit", "absent") for h in ours]
    assert flags[:2] == [True, False]
    assert set(flags[2:]) <= {"absent"}  # a track Spotify sent no flag for: no key
    sent = [r for r in fake.requests("api.spotify.com") if r.path.endswith("/search")]
    assert sent[0].query == sent[-1].query  # the same request, twice


def test_the_librarys_pieces_are_what_the_search_relies_on() -> None:
    assert spotify_web.SEARCH_LIMIT == 10
    assert list(inspect.signature(spotify_web.call).parameters) == [
        "creds",
        "method",
        "path",
        "params",
        "body",
    ]
    assert list(inspect.signature(reduce).parameters) == ["t"]
    assert "explicit" not in inspect.getsource(reduce)  # when it does, this search can go


# --- the registry --------------------------------------------------------------------------------


def hit(n: int, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"ExplicitTrk{n:011d}",
        "title": f"Song {n}",
        "artists": ["A Band"],
        "album": "An Album",
        "year": "2001",
        "sec": 200,
        **extra,
    }


def test_the_registry_keeps_a_boolean_flag_and_never_shows_it() -> None:
    reg = SearchRegistry()
    entries = reg.add_hits(
        [hit(0, explicit=True), hit(1, explicit=False), hit(2), hit(3, explicit="true")]
    )
    assert [e.explicit for e in entries] == [True, False, None, None]
    assert "explicit" not in json.dumps([e.shown() for e in entries])
    assert "explicit" not in repr(entries)  # no log or traceback line has it
