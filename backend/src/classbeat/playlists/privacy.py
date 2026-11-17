"""Ask once more for privacy when Spotify made a playlist classbeat just created public (#1155,
roadmap Amendment 27, ADR 0008 section 2).

A live run showed a playlist created with ``POST /me/playlists`` and ``public: false`` read back
public. The owner accepts a public working or final playlist (Amendment 27), so this step is best
effort and never fails the job. ``ensure_private`` runs right after a create, **only** on the id
the create operation itself returned (the pool's and a class's final playlist; never an id from
input, a listing, or a stored row of another job):

1. read the playlist (``GET /playlists/{id}``); ``public`` and ``collaborative`` both exactly
   ``false``, or the read fails: nothing more;
2. this id, owned by the connected user, but ``public`` or ``collaborative`` not exactly
   ``false``: the decide step (``guard``, in a fenced transaction: the held connection and the row
   that names this playlist), then the change-details call ``PUT /playlists/{id}`` with
   ``{"public": false, "collaborative": false}`` (absolute, so repeating it is safe), then one
   read-back, logged;
3. whatever came of it, the first item write's owner check (``replace``, ``common.owned``) is the
   gate: it refuses another owner, another id and a collaborative playlist, and writes nothing.

No operation row of its own: the call only changes the privacy of a playlist this job's own create
made, towards private, and it is absolute; a retried job replays the create's ``succeeded`` row and
runs this again. Nothing is logged but the purpose and the outcome.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final, cast

from mowl import spotify_web
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import ConnectionFailure, Credentials, OpenConnection
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobFailure, JobRun
from classbeat.playlists import common

logger = logging.getLogger(__name__)

PRIVATE_BODY: Final = {"public": False, "collaborative": False}
# The read, the change and the read-back, one request each.
BOUND_SECONDS: Final = 3 * common.REQUEST_SECONDS

Decide = Callable[[AsyncSession], Awaitable[bool]]


def is_private(found: object) -> bool:
    """``public`` and ``collaborative`` both exactly ``false``."""
    if not isinstance(found, Mapping):
        return False
    playlist = cast(Mapping[str, Any], found)
    return playlist.get("public") is False and playlist.get("collaborative") is False


def fixable(found: object, playlist_id: str, account: str | None) -> bool:
    """This playlist, owned by the connected user, its privacy not exactly private: the one case
    the change is sent for."""
    if account is None or not isinstance(found, Mapping) or is_private(found):
        return False
    playlist = cast(Mapping[str, Any], found)
    return playlist.get("id") == playlist_id and playlist.get("owner_id") == account


def _make_private(playlist_id: str) -> Callable[[Credentials], Any]:
    pid = common.check_playlist_id(playlist_id)

    def fn(creds: Credentials) -> Any:
        # The library has no helper for the change-details call; its ``call`` sends it to the
        # allowlisted API host like every other request of the library.
        return spotify_web.call(creds, "PUT", f"playlists/{pid}", body=dict(PRIVATE_BODY))

    return fn


async def ensure_private(
    run: JobRun,
    keyring: KeyRing,
    opened: OpenConnection,
    playlist_id: str,
    purpose: str,
    guard: Decide,
) -> bool:
    """Steps 1 and 2 for ``playlist_id``, the create's own answer. True when the change was sent.
    Raises only from the decide step (``guard``), never for a playlist that stayed public."""
    playlist_id = common.check_playlist_id(playlist_id)
    deadline_at = time.monotonic() + BOUND_SECONDS

    async def run_call(fn: Callable[[Credentials], Any], *, write: bool = False) -> object:
        return await common.call(
            run.factory, keyring, run.ctx, opened, fn, write=write, deadline_at=deadline_at
        )

    try:
        found = await run_call(lambda c: spotify_web.get_playlist(c, playlist_id))
    except ConnectionFailure:
        return False  # the write's owner check reads it again
    if not fixable(found, playlist_id, opened.account):
        return False
    async with run.fenced() as fs:
        if not await guard(fs.db):
            raise JobFailure("not_connected")
    try:
        await run_call(_make_private(playlist_id), write=True)
        after = await run_call(lambda c: spotify_web.get_playlist(c, playlist_id))
    except ConnectionFailure:
        after = None
    outcome = "private" if is_private(after) else "still_not_private"
    logger.warning(
        "new spotify playlist was not private", extra={"purpose": purpose, "outcome": outcome}
    )
    return True
