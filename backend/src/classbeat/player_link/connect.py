"""Linking classbeat-player (ADR 0034 section 3): the start, the callback and the disconnect.

**Start** creates the flow row (state and session as SHA-256, the PKCE verifier, the **fixed**
redirect address ``<issuer>/connections/player/callback``) and returns the player's
``/link/source`` address with ``login_hint`` = the user's pairwise subject. Nothing about an
existing connection changes at the start.

**Callback** (ADR 0009 section 4's rules). Without a session nothing is deleted. A ``state``
that is not the caller's own (unknown, another user's, another session's) deletes nothing and
that flow stays valid for its owner. Otherwise:

- an ``error`` from the player, a missing ``code``, or a request that did not arrive at the
  fixed callback deletes the caller's own flow and ends ``access_denied``; no call is made;
- the flow is taken atomically and single use; an own flow that is expired or used is deleted
  (``access_denied``); the stored redirect address is re-checked equal to the fixed one; a code
  that is not ``cbp_lc_`` is never sent;
- the code is exchanged at ``POST /api/source/token`` with the verifier;
- **the binding**: the token is kept only if ``account_subject`` is classbeat's own issuer and
  the user's pairwise subject. Otherwise ``DELETE /api/source/link`` is called with that token
  at once, nothing is stored and the answer is ``wrong_account``;
- **one at a time**: from here on the callback holds the user's link lock (taken without
  waiting). A second callback of the same user that finds it held ends its own new link at once
  and answers ``link_in_progress``, so an old link is ended once and no new link is overwritten
  and left orphaned on the player;
- **reconnect** (a row exists): the old link is ended first with the old token, then the row is
  replaced in one transaction that also forgets the pending, failed and uncertain player
  operations. If the old ``DELETE`` fails (or the old token cannot be opened), the reconnect
  still completes and the page says the old link may still be listed on the player's site.
  Should storing the new token fail, the new link is ended so no unreachable link is left, and
  an old row whose link is no longer usable becomes ``needs_reconnect`` (never ``connected``
  with a revoked token).

**Disconnect**: ``DELETE /api/source/link`` with the stored token, then the row is deleted in
any case (a ``401`` means it was already gone; no answer or a ``5xx`` gives the same "may still
be listed" note). Without a player client configured, the row is still deleted with that note.

The token never goes to a log, a model or the browser; the taken flow is always deleted.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.auth import PlayerBinding
from classbeat.connections.player_account import (
    PlayerLink,
    mark_player_link_ended,
    player_link_lock,
    read_player_link,
    remove_player_link,
    store_player_link,
)
from classbeat.connections.player_flows import (
    TakenLinkFlow,
    create_link_flow,
    delete_own_link_flow,
    take_link_flow,
)
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.player_link.link_policy import (
    CONNECTED,
    IN_PROGRESS,
    INVALID_RESPONSE,
    MISMATCH,
    STATE_MAX,
    WRONG_ACCOUNT,
    callback_uri,
    decide,
    is_token,
    link_url,
    new_state,
    new_verifier,
    session_hash,
    sha256,
)
from classbeat.player_link.source_http import Ended, PlayerSourceHttp

logger = logging.getLogger(__name__)
Factory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Result:
    """``category`` is ``connected`` or a ``link_policy`` outcome; ``old_link_listed`` says a
    reconnect could not end the old link."""

    category: str
    old_link_listed: bool = False

    @property
    def connected(self) -> bool:
        return self.category == CONNECTED


@dataclass(frozen=True, slots=True)
class Disconnected:
    removed: bool
    link_may_remain: bool


async def start(
    factory: Factory,
    http: PlayerSourceHttp,
    ctx: UserContext,
    session_id: object,
    binding: PlayerBinding,
) -> str:
    state, verifier = new_state(), new_verifier()
    redirect_uri = callback_uri(binding.issuer)
    await create_link_flow(
        factory,
        ctx,
        state_hash=sha256(state),
        session_hash=session_hash(session_id),
        verifier=verifier,
        redirect_uri=redirect_uri,
    )
    return link_url(
        player_base_url=http.base_url,
        redirect_uri=redirect_uri,
        state=state,
        verifier=verifier,
        login_hint=binding.subject,
    )


async def finish(
    factory: Factory,
    ring: KeyRing,
    http: PlayerSourceHttp,
    ctx: UserContext | None,
    session_id: object | None,
    binding: PlayerBinding | None,
    *,
    at_fixed_callback: bool,
    code: str | None,
    state: str | None,
    error: str | None,
) -> Result:
    if ctx is None or session_id is None or binding is None:
        return Result(MISMATCH)
    if not state or len(state) > STATE_MAX:
        return Result(MISMATCH)
    own = {"state_hash": sha256(state), "session_hash": session_hash(session_id)}
    if error is not None or not code or not at_fixed_callback:
        await delete_own_link_flow(factory, ctx, **own)
        return Result(MISMATCH)
    taken = await take_link_flow(factory, ctx, **own)
    if taken is None:
        # The caller's own flow, if this state names one, is expired or used: delete it.
        await delete_own_link_flow(factory, ctx, **own)
        return Result(MISMATCH)
    try:
        if taken.redirect_uri != callback_uri(binding.issuer) or not is_token("lc", code):
            return Result(MISMATCH)
        return await _link(factory, ring, http, ctx, binding, taken, code)
    finally:
        await delete_own_link_flow(factory, ctx, **own)


async def _link(
    factory: Factory,
    ring: KeyRing,
    http: PlayerSourceHttp,
    ctx: UserContext,
    binding: PlayerBinding,
    taken: TakenLinkFlow,
    code: str,
) -> Result:
    exchanged = await http.exchange(
        code=code, redirect_uri=taken.redirect_uri, verifier=taken.verifier
    )
    if exchanged.category is not None or exchanged.answer is None:
        return Result(exchanged.category or INVALID_RESPONSE)
    decision = decide(exchanged.answer, issuer=binding.issuer, subject=binding.subject)
    if decision.action == "invalid" or decision.token is None:
        return Result(INVALID_RESPONSE)
    if decision.action == "end":
        await http.end_link(decision.token)
        return Result(WRONG_ACCOUNT)
    async with player_link_lock(factory, ctx) as held:
        if not held:
            await http.end_link(decision.token)
            return Result(IN_PROGRESS)
        return await _replace(factory, ring, http, ctx, decision.token, decision.link_id)


async def _replace(
    factory: Factory,
    ring: KeyRing,
    http: PlayerSourceHttp,
    ctx: UserContext,
    token: str,
    link_id: str | None,
) -> Result:
    """Under the user's link lock: end the old link, then store the new one."""
    old = await read_player_link(factory, ring, ctx)
    ended: Ended | None = None
    if old is not None and old.token is not None:
        ended = await http.end_link(old.token)
    try:
        await store_player_link(factory, ring, ctx, token, link_id)
    except Exception:
        await http.end_link(token)
        if old is not None and ended != "failed":  # its link is gone or its token unusable
            await _mark_quietly(factory, ctx, old)
        raise
    return Result(CONNECTED, old is not None and (old.token is None or ended == "failed"))


async def _mark_quietly(factory: Factory, ctx: UserContext, old: PlayerLink) -> None:
    """The store's own error is the one raised; a failed mark only adds a warning."""
    try:
        await mark_player_link_ended(factory, ctx, old)
    except Exception:
        logger.warning("player link left connected after a failed store")


async def disconnect(
    factory: Factory, ring: KeyRing, http: PlayerSourceHttp | None, ctx: UserContext
) -> Disconnected:
    link = await read_player_link(factory, ring, ctx)
    if link is None:
        return Disconnected(removed=False, link_may_remain=False)
    may_remain = True
    if link.token is not None and http is not None:
        may_remain = await http.end_link(link.token) == "failed"
    removed = await remove_player_link(factory, ctx, link)
    return Disconnected(removed=removed, link_may_remain=may_remain)
