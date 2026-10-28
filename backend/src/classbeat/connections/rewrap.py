"""``keys rewrap`` for connection rows (ADR 0004 §2, rewrap mechanics).

Per owner, as ``classbeat_app`` with ``app.user_id`` set (no RLS bypass): the CLI gets one
context per user from ``classbeat.auth``. Each row is re-wrapped in its own transaction:
``SELECT ... FOR UPDATE``, unwrap under its KEK, wrap under the current one, then
``UPDATE ... WHERE id = :id AND key_id = :old_key_id``. The data key and the ciphertext stay the
same: only the wrap changes. Counts per ``key_id`` only; never a key, an id or a payload.
"""

import logging
from collections import Counter
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.connections.tables import connections
from classbeat.context import UserContext
from classbeat.crypto.envelope import DecryptFailed, connection_wrap_aad, rewrap
from classbeat.crypto.keks import KeyRing, KeyUnavailable
from classbeat.db import user_session

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]
_C = connections.c


async def rows_by_key(factory: Factory, ctx: UserContext) -> Counter[str]:
    """How many of the user's rows each ``key_id`` wraps."""
    async with user_session(factory, ctx) as db:
        rows = await db.execute(
            select(_C.key_id, func.count()).where(_C.owner_id == ctx.user_id).group_by(_C.key_id)
        )
        return Counter({row[0]: row[1] for row in rows})


async def rewrap_owner(factory: Factory, ring: KeyRing, ctx: UserContext) -> Counter[str]:
    """Move every row of the user that is not under the current KEK onto it.

    Returns counts keyed ``"<old key_id> <outcome>"`` with the outcomes ``rewrapped``,
    ``key_unavailable`` (the old KEK is missing or rejected: the row is left as it is),
    ``decrypt_failed`` (the row flips to ``needs_reconnect``) and ``skipped`` (it changed
    meanwhile: a reconnect already wrote it under the current KEK, or another rewrap moved it).
    """
    current = ring.current_id
    async with user_session(factory, ctx) as db:
        pending = (
            (
                await db.execute(
                    select(_C.id).where(_C.owner_id == ctx.user_id, _C.key_id != current)
                )
            )
            .scalars()
            .all()
        )
    counts: Counter[str] = Counter()
    for row_id in pending:
        old_key_id, outcome = await _rewrap_row(factory, ring, ctx, row_id)
        counts[f"{old_key_id} {outcome}"] += 1
    return counts


async def _rewrap_row(
    factory: Factory, ring: KeyRing, ctx: UserContext, row_id: UUID
) -> tuple[str, str]:
    async with user_session(factory, ctx) as db:
        current = await ring.current(db)
        row = (
            await db.execute(
                select(_C.provider, _C.key_id, _C.wrapped_dek, _C.dek_nonce)
                .where(_C.id == row_id, _C.owner_id == ctx.user_id, _C.key_id != current.key_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            return "-", "skipped"
        try:
            old = await ring.kek_for(db, row.key_id)
        except KeyUnavailable:
            return row.key_id, "key_unavailable"
        moved: tuple[bytes, bytes] | None = None
        try:
            moved = rewrap(
                bytes(row.wrapped_dek),
                bytes(row.dek_nonce),
                old,
                connection_wrap_aad(old.key_id, ctx.user_id, row.provider, row_id),
                current,
                connection_wrap_aad(current.key_id, ctx.user_id, row.provider, row_id),
            )
        except DecryptFailed:
            moved = None
        if moved is None:
            await db.execute(
                update(connections)
                .where(
                    _C.id == row_id,
                    _C.owner_id == ctx.user_id,
                    _C.wrapped_dek == row.wrapped_dek,
                )
                .values(status="needs_reconnect", last_error="decrypt_failed")
            )
            logger.warning(
                "connection unusable",
                extra={"category": "decrypt_failed", "connection_id": str(row_id)},
            )
            return row.key_id, "decrypt_failed"
        result = await db.execute(
            update(connections)
            .where(_C.id == row_id, _C.owner_id == ctx.user_id, _C.key_id == row.key_id)
            .values(wrapped_dek=moved[0], dek_nonce=moved[1], key_id=current.key_id)
            .returning(_C.id)
        )
        return row.key_id, ("rewrapped" if result.first() is not None else "skipped")
