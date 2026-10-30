"""Connecting Spotify (ADR 0009 sections 3 to 5 and 7): the start and the callback.

**Start** creates the flow row (state and session as SHA-256, the Client ID, the **fixed**
redirect URI from settings, the PKCE verifier, ``confirm_account_change``) and returns the
authorize URL. Nothing about an existing connection changes at the start: with another Client ID
the old tokens stay in use until a new flow succeeds.

**Callback.** Without a session nothing is deleted (no flow can be the caller's). A ``state``
that is not the caller's own (unknown, another user's, another session's) deletes nothing and
that flow stays valid for its owner. Otherwise:

- an ``error`` from Spotify, a missing ``code``, or a request that did not arrive at the fixed
  callback deletes the caller's own flow and ends ``access_denied`` (``app_misconfigured`` for
  Spotify's app-setup errors on the caller's own flow); no request is made;
- the flow is taken atomically and single-use (``connections.take_flow``); an own flow that is
  expired or used is deleted (``access_denied``);
- the stored ``redirect_uri`` is re-checked equal to the setting (never taken from the request);
- the code is exchanged with the stored Client ID, redirect URI and verifier (no Client Secret),
  the granted scope checked, ``GET /me`` verifies the token and gives the Spotify user id, and
  ``connections.save`` stores the tokens. Under the row lock it decides between a reconnect, a
  Client ID change (``generation`` + 1, the pool forgotten) and another account (only with the
  flow's ``confirm_account_change``, else ``account_changed`` and nothing stored).

On any failure nothing is stored and the tokens are dropped; the taken flow is deleted. Every
mismatch gives the same answer. Nothing here logs a code, a state, a verifier or a token.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import connections
from classbeat.config import Settings
from classbeat.connections import ConnectionFailure
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.egress import OutboundBlocked
from classbeat.spotify.errors import APP_ERROR_CODES
from classbeat.spotify.flow import (
    STATE_MAX,
    authorize_url,
    new_state,
    new_verifier,
    scope_granted,
    session_hash,
    sha256,
)
from classbeat.spotify.http import SpotifyHttp

logger = logging.getLogger(__name__)

PROVIDER = "spotify"
CONNECTED = "connected"
Factory = async_sessionmaker[AsyncSession]
MISMATCH = "access_denied"  # the one answer of every mismatch (section 4)


@dataclass(frozen=True, slots=True)
class Result:
    """``category`` is ``connected`` or a category of section 8."""

    category: str
    retry_after: int | None = None

    @property
    def connected(self) -> bool:
        return self.category == CONNECTED


async def start(
    factory: Factory,
    settings: Settings,
    ctx: UserContext,
    session_id: object,
    client_id: str,
    *,
    confirm_account_change: bool,
) -> str:
    """A new flow for ``client_id`` (already validated); returns the authorize URL."""
    state, verifier = new_state(), new_verifier()
    redirect_uri = settings.spotify_redirect_uri
    await connections.create_flow(
        factory,
        ctx,
        state_hash=sha256(state),
        session_hash=session_hash(session_id),
        client_id=client_id,
        redirect_uri=redirect_uri,
        verifier=verifier,
        confirm_account_change=confirm_account_change,
        ttl_seconds=settings.spotify_flow_ttl_seconds,
    )
    return authorize_url(
        client_id=client_id, redirect_uri=redirect_uri, state=state, verifier=verifier
    )


async def finish(
    factory: Factory,
    ring: KeyRing,
    settings: Settings,
    http: SpotifyHttp,
    ctx: UserContext | None,
    session_id: object | None,
    *,
    at_fixed_callback: bool,
    code: str | None,
    state: str | None,
    error: str | None,
) -> Result:
    """The callback's whole decision (see the module docstring)."""
    if ctx is None or session_id is None or not state or len(state) > STATE_MAX:
        return Result(MISMATCH)
    own = {"state_hash": sha256(state), "session_hash": session_hash(session_id)}
    if error is not None or not code or not at_fixed_callback:
        deleted = await connections.delete_own_flow(factory, ctx, **own)
        if deleted and at_fixed_callback and error in APP_ERROR_CODES:
            return Result("app_misconfigured")
        return Result(MISMATCH)
    taken = await connections.take_flow(factory, ctx, **own)
    if taken is None:
        # The caller's own flow, if this state names one, is expired or used: delete it.
        # Another user's or session's flow is not matched and stays valid.
        await connections.delete_own_flow(factory, ctx, **own)
        return Result(MISMATCH)
    try:
        if taken.redirect_uri != settings.spotify_redirect_uri:
            return Result(MISMATCH)
        return await _connect(factory, ring, http, ctx, taken, code)
    finally:
        await connections.delete_own_flow(factory, ctx, **own)


async def _connect(
    factory: Factory,
    ring: KeyRing,
    http: SpotifyHttp,
    ctx: UserContext,
    taken: connections.TakenFlow,
    code: str,
) -> Result:
    failure: ConnectionFailure | None = None
    blocked = False
    try:
        granted = await http.exchange_code(
            client_id=taken.client_id,
            code=code,
            redirect_uri=taken.redirect_uri,
            verifier=taken.verifier,
        )
        if not scope_granted(granted.scope):
            raise ConnectionFailure("scope_mismatch")
        if granted.refresh_token is None:
            raise ConnectionFailure("invalid_response")
        spotify_user_id = await http.me(granted.access_token)
        await connections.save(
            factory,
            ring,
            ctx,
            PROVIDER,
            {
                "access_token": granted.access_token,
                "refresh_token": granted.refresh_token,
                "expires_at": connections.expires_at_of(granted.expires_in, time.time()),
            },
            public={"client_id": taken.client_id, "spotify_user_id": spotify_user_id},
            confirm_account_change=taken.confirm_account_change,
        )
    except ConnectionFailure as error:
        failure = error
    except OutboundBlocked:
        blocked = True
    if blocked:
        return Result("upstream_unavailable")
    if failure is not None:
        retry = (
            failure.retry_after if failure.category in ("rate_limited", "quota_exhausted") else None
        )
        return Result(failure.category, retry)
    return Result(CONNECTED)
