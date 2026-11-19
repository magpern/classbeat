"""The one request of ``spotify_create_playlist`` (``classbeat.playlists.create``): ``POST
/me/playlists`` with no tracks, ``public: true`` and ``collaborative: false`` (roadmap Amendment
30, #1287).

MOWL's Spotify import lists only the user's public playlists that are on their Spotify profile,
and Spotify documents ``public`` as exactly that ("if it should be added to the user's profile or
not"); the Web API has no other field for the profile. So a working or final playlist is created
public in this one call and nothing asks for private afterwards or reads it back for its privacy.
"""

from typing import Any, Final, cast

from mowl import spotify_web
from mowl.errors import MowlError

from classbeat.connections import ConnectionFailure, Credentials
from classbeat.playlists import common


class AnswerUnreadable(Exception):
    """Spotify's answer to the create had no valid id, or the library refused it: the playlist
    may exist. Not the library's error type, so it is never taken for a refusal (``uncertain``)."""


# Never collaborative: the owner check refuses one.
PUBLIC_BODY: Final = {"public": True, "collaborative": False}


# A create refused with 403: Spotify needs ``playlist-modify-public`` for a public playlist, and a
# connection made before Amendment 27 has only the two private scopes. One POST refused proves
# nothing was created, so the row is ``failed``; the sentence asks the user to connect again.
NO_PUBLIC_SCOPE: Final = "scope_mismatch"


def no_public_scope(failure: ConnectionFailure) -> bool:
    """The create was refused with 403 (proven not applied): the public scope is missing."""
    return failure.http_status == 403 and failure.not_applied


def create_body(name: str, about: str) -> dict[str, object]:
    """The body of ``POST /me/playlists``: the name, the description and ``PUBLIC_BODY``."""
    return {"name": name, "description": about, **PUBLIC_BODY}


def create(name: str, about: str) -> Any:
    """The call ``common.call`` runs with the held connection; the new playlist's id."""
    body = create_body(name, about)

    def fn(creds: Credentials) -> str:
        # The library's ``create_playlist`` always asks for private; its ``call`` sends this one
        # request to the allowlisted API host like every other request of the library.
        try:
            answer = spotify_web.call(creds, "POST", "me/playlists", body=dict(body))
        except MowlError as exc:
            if exc.__cause__ is None:
                # No HTTP cause: the library refused the token before sending, or the answer.
                # The second may have created the playlist: never a refusal.
                raise AnswerUnreadable from None
            raise
        found = cast(dict[str, Any], answer).get("id") if isinstance(answer, dict) else None
        if not isinstance(found, str) or common.SPOTIFY_ID.fullmatch(found) is None:
            raise AnswerUnreadable  # sent and answered without a valid id: it may exist
        return found

    return fn
