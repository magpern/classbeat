"""The ``player`` connection row (ADR 0034 section 3; ADR 0004): the encrypted ``cbp_src_`` source
token, and ``public`` with the player's link id when the player names one.

- ``read_player_link``: the user's row and its decrypted token, for the reconnect's and the
  disconnect's ``DELETE /api/source/link``. A row that cannot be opened (its key unavailable,
  a failed decryption, a malformed payload) comes back with ``token=None``: the caller still
  ends the row locally and says the link may still be listed. Its status does not matter (a
  ``needs_reconnect`` row's token may still end the link).
- ``store_player_link``: connect or reconnect through ``repository.save``, which keeps the row
  id, increases ``generation`` and, for ``player``, forgets the pending, failed and uncertain
  player operations in the same transaction (``player_data``).
- ``remove_player_link``: delete the row read earlier, only while it is still that
  ``generation`` (a reconnect in between keeps its new row and its new link), with the row lock
  first and the player operations forgotten in the same transaction.
- ``mark_player_link_ended``: ``needs_reconnect`` for the row read earlier (same generation),
  when its link was ended but no new token could be stored, so no row stays ``connected`` with
  a token the player has revoked.
- ``hold_player_link`` and ``player_connected`` (ADR 0034 section 5), in the caller's
  transaction: the row read earlier share-locked while it is still that generation and
  ``connected`` (a send's and a removal's decide step), and whether the user's row is
  ``connected`` now (admit, a class's deletion).
- ``player_link_lock``: the user's transaction-level advisory lock for a reconnect, taken
  without waiting (``pg_try_advisory_xact_lock``; a wait would run into the role's
  ``lock_timeout``). It serializes two callbacks of one user, so one old link is ended once and
  no new link is overwritten and left orphaned on the player.

Nothing here logs or returns the token except to the caller that sends it to the player.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import NoReturn
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.connections.player_data import forget_player_operations
from classbeat.connections.repository import save
from classbeat.connections.row_crypto import open_row
from classbeat.connections.tables import connections
from classbeat.context import UserContext
from classbeat.crypto.keks import KeyRing
from classbeat.db import user_session

PROVIDER = "player"
Factory = async_sessionmaker[AsyncSession]
_C = connections.c
# The lock's key namespace, so it never shares a key with another advisory lock.
LOCK_NAMESPACE = "classbeat.player.link:"
_TRY_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")
_HOLD = text(
    "SELECT 1 FROM connections WHERE owner_id = :owner_id AND id = :id AND provider = 'player' "
    "AND generation = :generation AND status = 'connected' FOR SHARE"
)
_CONNECTED = text(
    "SELECT 1 FROM connections WHERE owner_id = :owner_id AND provider = 'player' "
    "AND status = 'connected' FOR SHARE"
)


@dataclass(frozen=True, slots=True, eq=False)
class PlayerLink:
    """The stored link: the row, its generation and, when it opened, the token."""

    id: UUID
    generation: int
    token: str | None

    def __repr__(self) -> str:
        return f"PlayerLink(id={self.id}, generation={self.generation}, token=...)"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a player link is never serialized")


def _token_of(plaintext: bytes | None) -> str | None:
    if plaintext is None:
        return None
    try:
        payload = json.loads(plaintext)
    except ValueError:
        return None
    if not isinstance(payload, dict) or set(payload) != {"token"}:  # pyright: ignore[reportUnknownArgumentType]
        return None
    token = payload["token"]  # pyright: ignore[reportUnknownVariableType]
    return token if isinstance(token, str) else None


async def read_player_link(factory: Factory, ring: KeyRing, ctx: UserContext) -> PlayerLink | None:
    async with user_session(factory, ctx) as db:
        row = (
            await db.execute(
                select(
                    _C.id,
                    _C.generation,
                    _C.secret,
                    _C.secret_nonce,
                    _C.wrapped_dek,
                    _C.dek_nonce,
                    _C.key_id,
                ).where(_C.owner_id == ctx.user_id, _C.provider == PROVIDER)
            )
        ).first()
        if row is None:
            return None
        _, plaintext = await open_row(db, ring, ctx.user_id, PROVIDER, row)
    return PlayerLink(row.id, int(row.generation), _token_of(plaintext))


async def store_player_link(
    factory: Factory, ring: KeyRing, ctx: UserContext, token: str, link_id: str | None
) -> UUID:
    public = {} if link_id is None else {"link_id": link_id}
    return await save(factory, ring, ctx, PROVIDER, {"token": token}, public=public)


async def remove_player_link(factory: Factory, ctx: UserContext, link: PlayerLink) -> bool:
    async with user_session(factory, ctx) as db:
        mine = (_C.id == link.id, _C.owner_id == ctx.user_id, _C.provider == PROVIDER)
        locked = (await db.execute(select(_C.generation).where(*mine).with_for_update())).first()
        if locked is None or int(locked.generation) != link.generation:
            return False
        await forget_player_operations(db, ctx.user_id)
        result = await db.execute(
            delete(connections).where(*mine, _C.generation == link.generation).returning(_C.id)
        )
        return result.first() is not None


async def mark_player_link_ended(factory: Factory, ctx: UserContext, link: PlayerLink) -> bool:
    async with user_session(factory, ctx) as db:
        result = await db.execute(
            update(connections)
            .where(
                _C.id == link.id,
                _C.owner_id == ctx.user_id,
                _C.provider == PROVIDER,
                _C.generation == link.generation,
            )
            .values(status="needs_reconnect", last_error="needs_reconnect", updated_at=func.now())
            .returning(_C.id)
        )
        return result.first() is not None


async def hold_player_link(db: AsyncSession, owner_id: UUID, link: PlayerLink) -> bool:
    """Share-lock ``link``'s row while it is that generation and ``connected``: no reconnect or
    disconnect can come between this check and the commit."""
    params = {"owner_id": owner_id, "id": link.id, "generation": link.generation}
    return (await db.execute(_HOLD, params)).first() is not None


async def player_connected(db: AsyncSession, owner_id: UUID) -> bool:
    """The user's player row is ``connected`` (share-locked until the caller commits)."""
    return (await db.execute(_CONNECTED, {"owner_id": owner_id})).first() is not None


@asynccontextmanager
async def player_link_lock(factory: Factory, ctx: UserContext) -> AsyncIterator[bool]:
    """``True`` while this caller holds the user's link lock; ``False`` if another holds it."""
    async with user_session(factory, ctx) as db:
        held = (await db.execute(_TRY_LOCK, {"key": f"{LOCK_NAMESPACE}{ctx.user_id}"})).scalar_one()
        yield bool(held)
