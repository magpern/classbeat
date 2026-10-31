"""Connection status for the admin area's user detail (ADR 0011 section 6, AD1 #218).

Which providers a user has connected and how they are, read on the session the caller hands in:
the admin's audited ``admin_session`` on ``classbeat_maint``, whose column grants on this table
are exactly the columns below. Never ``public`` (the Spotify Client ID), a ciphertext, nonce,
wrapped key or key id. Unlike the rest of this package it takes no ``UserContext``: the admin
reads another user's status, and opens no session of its own.

A Spotify row exists only once a Client ID is set and connected, so its presence answers "is a
Client ID set" (ADR 0009 section 8) without reading the ID.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.tables import PROVIDERS, connections

NOT_CONNECTED = "not_connected"


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    provider: str
    status: str  # connected, needs_reconnect or not_connected
    last_error: str | None  # an error category (ADR 0004 section 5), never a message
    connected_at: datetime | None
    last_used_at: datetime | None


async def connection_statuses(db: AsyncSession, owner_id: UUID) -> list[ConnectionStatus]:
    """One entry per provider, in ``PROVIDERS`` order; ``not_connected`` without a row."""
    rows = await db.execute(
        select(
            connections.c.provider,
            connections.c.status,
            connections.c.last_error,
            connections.c.connected_at,
            connections.c.last_used_at,
        ).where(connections.c.owner_id == owner_id)
    )
    found = {row.provider: row for row in rows.all()}
    statuses: list[ConnectionStatus] = []
    for provider in PROVIDERS:
        row = found.get(provider)
        statuses.append(
            ConnectionStatus(provider, NOT_CONNECTED, None, None, None)
            if row is None
            else ConnectionStatus(
                provider, row.status, row.last_error, row.connected_at, row.last_used_at
            )
        )
    return statuses
