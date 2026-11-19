"""The one request of ``spotify_create_playlist`` (``classbeat.playlists.create``): ``POST
/me/playlists`` with no tracks, ``public: true`` and ``collaborative: false`` (roadmap Amendment
30, #1287).

MOWL's Spotify import lists only the user's public playlists that are on their Spotify profile,
and Spotify documents ``public`` as exactly that ("if it should be added to the user's profile or
not"); the Web API has no other field for the profile. So a working or final playlist is created
public in this one call and nothing asks for private afterwards or reads it back for its privacy.

The library's public ``create_playlist`` always asks for private, so this calls the helper it is
built on, ``spotify_web._new_playlist`` (one ``POST`` through the library's ``call``, to the
allowlisted API host; it always sends ``collaborative: false`` and checks the answered id). The
vendored wheel is pinned by hash and ``tests/test_mowl_library.py`` pins that helper's signature.
"""

from typing import Any, Final

from mowl import spotify_web
from mowl.errors import MowlError

from classbeat.connections import Credentials

# The privacy every create asks for. The library adds ``collaborative: false`` (the owner check
# refuses a collaborative playlist).
PUBLIC: Final = True


class AnswerUnreadable(Exception):
    """Spotify's answer to the create had no valid id, or the library refused it: the playlist
    may exist. Not the library's error type, so it is never taken for a refusal (``uncertain``)."""


def create(name: str, about: str) -> Any:
    """The call ``common.call`` runs with the held connection; the new playlist's id."""

    def fn(creds: Credentials) -> str:
        try:
            return spotify_web._new_playlist(creds, name, about, public=PUBLIC)  # pyright: ignore[reportPrivateUsage]
        except MowlError as exc:
            if exc.__cause__ is None:
                # No HTTP cause: the library refused the answer (no valid id) or the token
                # before sending. The first may have created the playlist: never a refusal.
                raise AnswerUnreadable from None
            raise

    return fn
