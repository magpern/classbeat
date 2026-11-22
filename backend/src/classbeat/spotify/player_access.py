"""``player_call``: the **player mode** of ``spotify_call`` in the web process, for the listening
preview's four requests (ADR 0042 section 7.2; it amends ADR 0009 sections 6 and 10).

Per request:

1. Open the row (``open_connection``; its own flip of a row whose secret no longer decrypts
   stays, ADR 0004 section 5). Only if the access token expires within the margin
   (``CLASSBEAT_SPOTIFY_REFRESH_MARGIN_SECONDS``) is it refreshed, under the row lock
   (``refresh_if_needed``: a second caller reuses the first one's refresh), then the row is
   opened again. A poll of a fresh token therefore never refreshes.
2. ``send(access_token)`` sends exactly one ``player_http`` request; the token goes nowhere
   else.
3. **Nothing is recorded**: no ``record_result``, no ``last_used_at``, no ``last_error``.
4. **A 401** forces one refresh, conditional on the row still holding the refused token (a
   refresh another caller made meanwhile is reused, no token request), and the request is sent
   **once more** with the row's token as it is now. Only a 401 on that repeat flips the
   connection to ``needs_reconnect`` (``invalid_grant``), conditional on ``id``, ``generation``
   and the ``wrapped_dek`` the repeat read, exactly as ADR 0009 section 6 does for a read; a
   stale 401 never flips a refreshed connection. Unlike a job's write, a play or pause is
   repeated: a 401 proves Spotify applied nothing, and the command sets the player's state
   (the same track and position, or paused), so sending it twice cannot do more than once.
5. Every other answer (2xx, 403, 404, 429, 5xx) is returned to the caller, who maps it; none
   flips the connection.

Every opening after the first must be the same row, generation and Spotify account (a reconnect
in between is ``not_connected`` and nothing more is sent). A refresh is never cancelled: it runs
under ``access.shielded`` (ADR 0009 section 6's amendment of 2026-11-03), so a browser that
leaves cannot lose a rotated refresh token.
"""

import time
from collections.abc import Awaitable, Callable

from classbeat import connections
from classbeat.connections import ConnectionFailure, open_connection
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.spotify.access import (
    PROVIDER,
    UNAUTHORIZED,
    Factory,
    expected_connection,
    expiring,
    refresh_if_needed,
    shielded,
)
from classbeat.spotify.http import SpotifyHttp
from classbeat.spotify.player_http import PlayerAnswer

Send = Callable[[str], Awaitable[PlayerAnswer]]


async def player_call(
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    send: Send,
    *,
    http: SpotifyHttp,
    margin_seconds: float,
) -> PlayerAnswer:
    """``send(access_token)`` for ``ctx``'s Spotify connection (module docstring).

    ``ConnectionFailure`` (``provider`` ``"spotify"``): ``not_connected``, ``key_unavailable``,
    ``decrypt_failed`` before any request; a refresh's own category; ``invalid_grant`` after the
    repeat's 401; ``upstream_unavailable`` for a transport failure. ``OutboundBlocked`` when the
    allowlist or the path guard refused a request."""
    try:
        return await _call(factory, ring, ctx, send, http, margin_seconds)
    except ConnectionFailure as failure:
        failure.provider = PROVIDER
        raise


async def _call(
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    send: Send,
    http: SpotifyHttp,
    margin_seconds: float,
) -> PlayerAnswer:
    first = await open_connection(factory, ring, ctx, PROVIDER)
    opened = first
    if expiring(opened, margin_seconds, time.time()):
        await shielded(refresh_if_needed(factory, ring, ctx, http, margin_seconds=margin_seconds))
        opened = expected_connection(await open_connection(factory, ring, ctx, PROVIDER), first)
    answer = await send(opened.credentials.spotify_access_token)
    if answer.status != UNAUTHORIZED:
        return answer
    refused = opened.credentials.spotify_access_token
    await shielded(
        refresh_if_needed(
            factory, ring, ctx, http, margin_seconds=margin_seconds, stale_access_token=refused
        )
    )
    repeat = expected_connection(await open_connection(factory, ring, ctx, PROVIDER), first)
    answer = await send(repeat.credentials.spotify_access_token)
    if answer.status != UNAUTHORIZED:
        return answer
    # The repeat, with the row's current token, was refused: only now does the status flip, and
    # only if nothing rewrote the row since the repeat opened it.
    await connections.mark_needs_reconnect(factory, ctx, repeat, "invalid_grant")
    raise ConnectionFailure("invalid_grant", not_applied=True, http_status=UNAUTHORIZED)
