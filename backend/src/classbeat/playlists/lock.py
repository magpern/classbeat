"""One Spotify playlist's writes, serialized (ADR 0036 section 9, SR C8).

``playlist_lock(factory, owner_id, key)`` holds a **session-level** advisory lock keyed by the
owner and the playlist (``working_copy`` for the user's pool, ``final:<class_playlists.id>`` for a
final playlist) for the whole write: the owner check, the replace and the read-back. A write that
is still in flight therefore always finishes before the next write to the same playlist starts,
also when its job lost the lease: the call runs on in its worker, which still holds the lock.

- The lock lives on a connection of its own, in autocommit, so it is never "idle in transaction"
  however long the write takes (the app role's ``idle_in_transaction_session_timeout``) and it
  never mixes with the job's fenced transactions. If that connection or its worker dies, Postgres
  releases the lock, and with the worker the call is gone too.
- It is taken without waiting on the server (``pg_try_advisory_lock``): the role's
  ``lock_timeout`` would end a server-side wait. The task polls every ``POLL_SECONDS`` for at most
  ``WAIT_SECONDS``; still held then, ``operation_busy`` (the runner requeues without an attempt).
- It is released when the block ends (and in any case by the pool's ``DISCARD ALL``).

Nothing here logs a key: it names a user and a playlist row.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.jobs import JobFailure

NAMESPACE: Final = "spotify_playlist"
WORKING_COPY: Final = "working_copy"  # the pool playlist's key
POLL_SECONDS: Final = 0.2
WAIT_SECONDS: Final = 120.0

_TRY = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")


def lock_name(owner_id: UUID, key: str) -> str:
    return f"{NAMESPACE}:{owner_id}:{key}"


@asynccontextmanager
async def playlist_lock(
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    key: str,
    *,
    wait_seconds: float = WAIT_SECONDS,
) -> AsyncIterator[None]:
    """Hold the playlist's write lock for the block (see the module docstring)."""
    name = {"key": lock_name(owner_id, key)}
    async with factory() as session:
        conn = await session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
        deadline = time.monotonic() + wait_seconds
        while not (await conn.execute(_TRY, name)).scalar_one():
            if time.monotonic() >= deadline:
                raise JobFailure("operation_busy")
            await asyncio.sleep(POLL_SECONDS)
        try:
            yield
        finally:
            await conn.execute(_UNLOCK, name)
