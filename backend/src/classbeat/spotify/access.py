"""``spotify_call``: the only way to call the Spotify API with a user's token (ADR 0009 section 6).

Per call:

1. Open the row (``open_connection``). If the access token expires within the margin
   (``CLASSBEAT_SPOTIFY_REFRESH_MARGIN_SECONDS``), refresh (``connections.refresh_spotify``, under
   the row lock) and open again.
2. The library gets ``Credentials`` with the Client ID and the **access token only**, renewal
   off and no 429 sleep (``connections.credentials``): it never contacts the token endpoint.
3. Run ``fn`` in a thread (``run_library_call``, the categories of section 8 via
   ``errors.classify_use``) and record the result (``record_result``, conditional on the
   generation read; a status flip never happens there).

**A 401** forces one refresh, conditional on the row still holding the access token that was
refused (if another caller refreshed meanwhile, no request). Then:

- a **read** is repeated once with the row as it is now; only a 401 on that repeat flips the
  connection (``invalid_grant``), conditional on ``id``, ``generation`` **and** the
  ``wrapped_dek`` read when the repeat opened the row, so a 401 sent with a token that is no
  longer the row's never flips a freshly refreshed connection;
- a **write** is never repeated: its 401 is a refusal (``upstream_unavailable``, ``not_applied``,
  retryable), the refresh has run, the status is not touched, and the job's next attempt writes
  with the new token.

``forbidden``, ``not_found`` and every other refusal never flip; ``invalid_grant`` and
``app_misconfigured`` from the token endpoint flip inside the refresh itself.
"""

import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import connections
from classbeat.connections import ConnectionFailure, Credentials, OpenConnection, open_connection
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.spotify.errors import classify_use
from classbeat.spotify.http import SpotifyHttp

PROVIDER = "spotify"
DEFAULT_REFRESH_MARGIN_SECONDS = 120
UNAUTHORIZED = 401
Factory = async_sessionmaker[AsyncSession]


def _expiring(opened: OpenConnection, margin_seconds: float, now: float) -> bool:
    return float(opened.credentials.spotify_expires_at) - now < margin_seconds


async def refresh_if_needed(
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    http: SpotifyHttp,
    *,
    margin_seconds: float = DEFAULT_REFRESH_MARGIN_SECONDS,
    stale_access_token: str | None = None,
) -> bool:
    """``connections.refresh_spotify`` with classbeat's own token client; True when a token
    request was sent."""
    return await connections.refresh_spotify(
        factory,
        ring,
        ctx,
        http.refresh,
        margin_seconds=margin_seconds,
        stale_access_token=stale_access_token,
    )


def _expected(opened: OpenConnection, expect: OpenConnection | None) -> OpenConnection:
    """The row as opened now must be the connection the caller held (``expect``): the same row,
    generation and Spotify account. A refresh keeps all three; a reconnect, a Client ID change or
    another account changes the generation, and then nothing is sent (``not_connected``)."""
    if expect is not None and (
        opened.id != expect.id
        or opened.generation != expect.generation
        or opened.account is None
        or opened.account != expect.account
    ):
        raise ConnectionFailure("not_connected")
    return opened


async def _attempt[T](
    factory: Factory,
    ctx: UserContext,
    opened: OpenConnection,
    fn: Callable[[Credentials], T],
    single_request: bool = False,
) -> tuple[T | None, ConnectionFailure | None]:
    """One library call. A 401 is returned unrecorded (the caller refreshes and decides); any
    other failure is recorded as ``last_error`` (never a flip)."""
    failure: ConnectionFailure | None = None
    try:
        result = await connections.run_library_call(
            lambda: fn(opened.credentials),
            sign_in=False,
            classifier=classify_use,
            single_request=single_request,
        )
    except ConnectionFailure as error:
        failure = error
    else:
        await connections.record_result(factory, ctx, opened, None)
        return result, None
    if failure.http_status != UNAUTHORIZED:
        await connections.record_result(factory, ctx, opened, failure.category)
    return None, failure


async def spotify_call[T](
    factory: Factory,
    ring: KeyRing,
    ctx: UserContext,
    fn: Callable[[Credentials], T],
    *,
    http: SpotifyHttp | None = None,
    write: bool = False,
    margin_seconds: float = DEFAULT_REFRESH_MARGIN_SECONDS,
    expect: OpenConnection | None = None,
    single_request: bool = False,
) -> T:
    """Run ``fn(credentials)`` (a ``mowl.spotify_web`` call) for ``ctx``'s Spotify connection.

    ``expect``: the connection the caller held (an external operation's decide step held it):
    every time the row is opened here it must still be that row, generation and Spotify account,
    or nothing is sent (``not_connected``). ``single_request``: ``fn`` is exactly one HTTP
    request, so a refusal of it proves no effect (``ConnectionFailure.not_applied``,
    ``run_library_call``).

    ``ConnectionFailure`` with a category of ADR 0009 section 8 (or ``not_connected``,
    ``key_unavailable``, ``decrypt_failed`` before any call); ``OutboundBlocked`` when the
    allowlist refused a request."""
    http = http if http is not None else SpotifyHttp()
    opened = _expected(await open_connection(factory, ring, ctx, PROVIDER), expect)
    if _expiring(opened, margin_seconds, time.time()):
        await refresh_if_needed(factory, ring, ctx, http, margin_seconds=margin_seconds)
        opened = _expected(await open_connection(factory, ring, ctx, PROVIDER), expect)
    result, failure = await _attempt(factory, ctx, opened, fn, single_request)
    if failure is None:
        return result  # pyright: ignore[reportReturnType]
    if failure.http_status != UNAUTHORIZED:
        raise failure
    # A 401: refresh, conditional on the row still holding the refused access token.
    await refresh_if_needed(
        factory,
        ring,
        ctx,
        http,
        margin_seconds=margin_seconds,
        stale_access_token=opened.credentials.spotify_access_token,
    )
    if write:
        raise ConnectionFailure("upstream_unavailable", not_applied=True, http_status=UNAUTHORIZED)
    repeat = _expected(await open_connection(factory, ring, ctx, PROVIDER), expect)
    result, failure = await _attempt(factory, ctx, repeat, fn, single_request)
    if failure is None:
        return result  # pyright: ignore[reportReturnType]
    if failure.http_status != UNAUTHORIZED:
        raise failure
    # The repeat, with the row's current token, was refused: only now does the status flip, and
    # only if nothing rewrote the row since the repeat opened it.
    await connections.mark_needs_reconnect(factory, ctx, repeat, "invalid_grant")
    raise ConnectionFailure("invalid_grant", not_applied=True, http_status=UNAUTHORIZED)
