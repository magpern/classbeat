"""``player_link_flows``: one in-flight link to classbeat-player (ADR 0034 section 3, modelled on
ADR 0009 sections 3 and 4 and ``spotify_flows``).

An owned table (migration 0050): every statement runs in the owner's ``user_session`` and names
``owner_id`` itself as well. The ``state`` and the session id are kept only as SHA-256 hashes;
the PKCE verifier is kept as given (useless without the one-time ``cbp_lc_`` code only the
user's browser receives, alive at most 10 minutes, deleted when used or expired).

- ``create_link_flow``: insert, then keep at most ``MAX_OPEN_FLOWS`` unexpired unused flows per
  user (a fourth start deletes the oldest) and drop the user's expired or used ones.
- ``take_link_flow``: the callback's atomic, single-use take, bound to the state, the user and
  the session. Another user's or another session's flow, or an unknown state, matches nothing.
- ``delete_own_link_flow``: only the row whose state, owner **and** session all match.
"""

from dataclasses import dataclass
from typing import NoReturn

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session

MAX_OPEN_FLOWS = 3
TTL_SECONDS = 600  # ADR 0034 section 3: 10 minutes (the table's check allows no more)
Factory = async_sessionmaker[AsyncSession]

_INSERT = text(
    "INSERT INTO player_link_flows (owner_id, state_hash, session_hash, verifier, redirect_uri, "
    "expires_at) VALUES (:owner_id, :state_hash, :session_hash, :verifier, :redirect_uri, "
    "now() + make_interval(secs => :ttl))"
)
_DROP_DEAD = text(
    "DELETE FROM player_link_flows WHERE owner_id = :owner_id "
    "AND (expires_at <= now() OR used_at IS NOT NULL)"
)
_TRIM = text(
    "DELETE FROM player_link_flows WHERE owner_id = :owner_id AND id IN ("
    "SELECT id FROM player_link_flows WHERE owner_id = :owner_id "
    "AND used_at IS NULL AND expires_at > now() "
    "ORDER BY created_at DESC, id DESC OFFSET :keep)"
)
_TAKE = text(
    "UPDATE player_link_flows SET used_at = now() "
    "WHERE state_hash = :state_hash AND owner_id = :owner_id AND session_hash = :session_hash "
    "AND used_at IS NULL AND expires_at > now() "
    "RETURNING verifier, redirect_uri"
)
_DELETE_OWN = text(
    "DELETE FROM player_link_flows "
    "WHERE state_hash = :state_hash AND owner_id = :owner_id AND session_hash = :session_hash "
    "RETURNING id"
)


@dataclass(frozen=True, slots=True, eq=False)
class TakenLinkFlow:
    """What the callback needs from its flow. Never printed (the verifier)."""

    verifier: str
    redirect_uri: str

    def __repr__(self) -> str:
        return "TakenLinkFlow(...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a flow is never serialized")


async def create_link_flow(
    factory: Factory,
    ctx: UserContext,
    *,
    state_hash: bytes,
    session_hash: bytes,
    verifier: str,
    redirect_uri: str,
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
                "verifier": verifier,
                "redirect_uri": redirect_uri,
                "ttl": float(TTL_SECONDS),
            },
        )
        await db.execute(_TRIM, {**owner, "keep": MAX_OPEN_FLOWS})


async def take_link_flow(
    factory: Factory, ctx: UserContext, *, state_hash: bytes, session_hash: bytes
) -> TakenLinkFlow | None:
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
    return TakenLinkFlow(str(row.verifier), str(row.redirect_uri))


async def delete_own_link_flow(
    factory: Factory, ctx: UserContext, *, state_hash: bytes, session_hash: bytes
) -> bool:
    async with user_session(factory, ctx) as db:
        found = await db.execute(
            _DELETE_OWN,
            {"state_hash": state_hash, "owner_id": ctx.user_id, "session_hash": session_hash},
        )
        return found.first() is not None
