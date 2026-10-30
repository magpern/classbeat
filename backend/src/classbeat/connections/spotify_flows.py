"""``spotify_oauth_flows``: one in-flight Spotify connect (ADR 0009 sections 3 and 4).

An owned table (migration 0006): every statement runs in the owner's ``user_session`` and names
``owner_id`` itself as well. The ``state`` and the session id are kept only as SHA-256 hashes;
the PKCE verifier is kept as given (useless without the one-time ``code`` that only the user's
browser receives, alive at most 10 minutes, deleted when used or expired).

- ``create_flow``: insert, then keep at most ``MAX_OPEN_FLOWS`` unexpired unused flows per user
  (a fourth start deletes the oldest) and drop the user's expired or used ones.
- ``take_flow``: the callback's atomic, single-use take, bound to the state, the user and the
  session. Another user's or another session's flow, or an unknown state, matches nothing.
- ``delete_own_flow``: what a mismatch or a finished callback deletes: only the row whose state,
  owner **and** session all match the request, never another user's or session's flow.
"""

from dataclasses import dataclass
from typing import NoReturn

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session

MAX_OPEN_FLOWS = 3
Factory = async_sessionmaker[AsyncSession]

_INSERT = text(
    "INSERT INTO spotify_oauth_flows (owner_id, state_hash, session_hash, client_id, "
    "redirect_uri, verifier, confirm_account_change, expires_at) VALUES (:owner_id, :state_hash, "
    ":session_hash, :client_id, :redirect_uri, :verifier, :confirm, "
    "now() + make_interval(secs => :ttl))"
)
_DROP_DEAD = text(
    "DELETE FROM spotify_oauth_flows WHERE owner_id = :owner_id "
    "AND (expires_at <= now() OR used_at IS NOT NULL)"
)
_TRIM = text(
    "DELETE FROM spotify_oauth_flows WHERE owner_id = :owner_id AND id IN ("
    "SELECT id FROM spotify_oauth_flows WHERE owner_id = :owner_id "
    "AND used_at IS NULL AND expires_at > now() "
    "ORDER BY created_at DESC, id DESC OFFSET :keep)"
)
_TAKE = text(
    "UPDATE spotify_oauth_flows SET used_at = now() "
    "WHERE state_hash = :state_hash AND owner_id = :owner_id AND session_hash = :session_hash "
    "AND used_at IS NULL AND expires_at > now() "
    "RETURNING client_id, redirect_uri, verifier, confirm_account_change"
)
_DELETE_OWN = text(
    "DELETE FROM spotify_oauth_flows "
    "WHERE state_hash = :state_hash AND owner_id = :owner_id AND session_hash = :session_hash "
    "RETURNING id"
)


@dataclass(frozen=True, slots=True, eq=False)
class TakenFlow:
    """What the callback needs from its flow. Never printed (the verifier)."""

    client_id: str
    redirect_uri: str
    verifier: str
    confirm_account_change: bool

    def __repr__(self) -> str:
        return "TakenFlow(...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a flow is never serialized")


async def create_flow(
    factory: Factory,
    ctx: UserContext,
    *,
    state_hash: bytes,
    session_hash: bytes,
    client_id: str,
    redirect_uri: str,
    verifier: str,
    confirm_account_change: bool,
    ttl_seconds: int,
) -> None:
    owner = {"owner_id": ctx.user_id}
    async with user_session(factory, ctx) as db:
        await db.execute(_DROP_DEAD, owner)
        await db.execute(
            _INSERT,
            {
                **owner,
                "state_hash": state_hash,
                "session_hash": session_hash,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "verifier": verifier,
                "confirm": confirm_account_change,
                "ttl": float(ttl_seconds),
            },
        )
        await db.execute(_TRIM, {**owner, "keep": MAX_OPEN_FLOWS})


async def take_flow(
    factory: Factory, ctx: UserContext, *, state_hash: bytes, session_hash: bytes
) -> TakenFlow | None:
    """The caller's own unexpired, unused flow for this state, marked used (committed at once,
    so a parallel callback with the same state finds it used)."""
    async with user_session(factory, ctx) as db:
        row = (
            await db.execute(
                _TAKE,
                {"state_hash": state_hash, "owner_id": ctx.user_id, "session_hash": session_hash},
            )
        ).first()
    if row is None:
        return None
    return TakenFlow(
        str(row.client_id),
        str(row.redirect_uri),
        str(row.verifier),
        bool(row.confirm_account_change),
    )


async def delete_own_flow(
    factory: Factory, ctx: UserContext, *, state_hash: bytes, session_hash: bytes
) -> bool:
    """Delete the flow only if its state, owner and session all match the request."""
    async with user_session(factory, ctx) as db:
        found = await db.execute(
            _DELETE_OWN,
            {"state_hash": state_hash, "owner_id": ctx.user_id, "session_hash": session_hash},
        )
        return found.first() is not None
