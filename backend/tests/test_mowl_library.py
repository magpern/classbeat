"""The pinned mowl-workouts has what Spotify song picking needs (ADR 0009 section 12, issue #173).

Fails on the 0.5.0 wheel: the functions, the ``Credentials`` options and ``SCOPES_PRIVATE`` came in
0.6.0. Offline: the calls below fail before any request (and the network guard refuses one anyway),
except the album image test (0.7.0, issue #242), which reaches the in-process fake Spotify through
the worker's allowlisted opener.
"""

import inspect
import urllib.request

import pytest
from mowl import spotify, spotify_web
from mowl.errors import MowlError

from classbeat.connections import Credentials
from spotifycore import NARROW_ALBUM, FakeSpotify, album_images, track_id
from spotifyfake import CALLBACK, CLIENT_A, USER_A, worker_opener

PLAYLIST = "p" * 21 + "9"


def hosted(**fields: object) -> Credentials:
    return Credentials(
        spotify_client_id="0" * 32,
        spotify_access_token="access",
        spotify_refresh_enabled=False,
        spotify_max_retry_wait=0,
        **fields,  # type: ignore[arg-type]
    )


def test_the_id_based_spotify_calls_exist() -> None:
    for name in (
        "playlist_items",
        "create_playlist",
        "add_items",
        "replace_playlist_items",
        "get_playlist",
        "me",
        "my_playlists",
        "search_tracks",
    ):
        assert callable(getattr(spotify_web, name)), name
    assert spotify_web.playlist_tracks is spotify_web.playlist_items
    assert "public" not in inspect.signature(spotify_web.create_playlist).parameters
    assert inspect.signature(spotify_web.create_playlist).parameters["uris"].default == ()
    assert inspect.signature(spotify_web.search_tracks).parameters["market"].default is None
    private = ["playlist-read-private", "playlist-modify-private"]
    assert spotify_web.SCOPES_PRIVATE.split() == private
    assert spotify.track_uri("t" * 22) == "spotify:track:" + "t" * 22
    with pytest.raises(MowlError):
        spotify.track_uri("../me")


def test_the_credentials_options_reach_the_library() -> None:
    creds = hosted(spotify_refresh_token="refresh", spotify_expires_at=0)
    assert creds.spotify_refresh_enabled is False
    assert creds.spotify_max_retry_wait == 0
    with pytest.raises(MowlError) as caught:
        spotify_web.call(creds, "GET", "me")  # expired, and renewal is off: no token request
    assert caught.value.__cause__ is None
    assert caught.value.status is None
    assert caught.value.retry_after is None


def test_a_bad_playlist_id_is_refused_before_any_request() -> None:
    creds = hosted(spotify_expires_at=1e12)
    for call in (
        lambda: spotify_web.replace_playlist_items(creds, PLAYLIST + "/x", []),
        lambda: spotify_web.get_playlist(creds, "../me"),
        lambda: spotify_web.playlist_items(creds, ""),
    ):
        with pytest.raises(MowlError, match="not a Spotify playlist ID"):
            call()


def test_track_entries_carry_the_smallest_album_image_of_at_least_64_px(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.7.0: M-1 and M-5 entries gain ``image`` from ``album.images`` (ADR 0013 section 9).
    classbeat ignores it until UI7."""
    fake = FakeSpotify()
    fake.add_user(USER_A)
    fake.add_app(CLIENT_A, redirect_uris=[CALLBACK], allowed=[USER_A])
    access, _ = fake.grant(CLIENT_A, USER_A)
    playlist = fake.add_playlist(USER_A, "Ride", [f"spotify:track:{track_id(0)}", None])
    monkeypatch.setattr(urllib.request, "_opener", worker_opener(fake))
    creds = Credentials(
        spotify_client_id=CLIENT_A,
        spotify_access_token=access,
        spotify_expires_at=1e12,
        spotify_refresh_enabled=False,
        spotify_max_retry_wait=0,
    )

    images = album_images(fake.catalogue[track_id(0)]["album_id"])
    assert [i["width"] for i in images] == [640, 300, 64]  # Spotify's order, largest first
    entries = spotify_web.playlist_items(creds, playlist.id)
    assert [e["image"] for e in entries] == [images[-1]["url"], None]  # a marker has none
    hits = spotify_web.search_tracks(creds, fake.catalogue[track_id(0)]["name"])
    assert hits
    assert all(
        h["image"] == album_images(fake.catalogue[h["id"]]["album_id"])[-1]["url"] for h in hits
    )
    assert {seen.host for seen in fake.seen} == {"api.spotify.com"}  # the image is never fetched

    # An album with only images under 64 px gives no image (the library's "else None", #251).
    fake.catalogue[track_id(0)]["album_id"] = NARROW_ALBUM
    assert [i["width"] for i in album_images(NARROW_ALBUM)] == [63, 32]
    assert [e["image"] for e in spotify_web.playlist_items(creds, playlist.id)] == [None, None]
    narrow = spotify_web.search_tracks(creds, fake.catalogue[track_id(0)]["name"])
    assert [h["image"] for h in narrow if h["id"] == track_id(0)] == [None]
