"""The pinned mowl-workouts has what Spotify song picking needs (ADR 0009 section 12, issue #173).

Fails on the 0.5.0 wheel: the functions, the ``Credentials`` options and ``SCOPES_PRIVATE`` came in
0.6.0. Offline: the calls below fail before any request (and the network guard refuses one anyway).
"""

import inspect

import pytest
from mowl import spotify, spotify_web
from mowl.errors import MowlError

from classbeat.connections import Credentials

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
