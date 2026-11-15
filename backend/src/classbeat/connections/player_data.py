"""What classbeat forgets when the player connection is replaced or ended (ADR 0034 section 3).

Like ADR 0007 section 9 for MOWL: a reconnect or a disconnect deletes the user's ``pending``,
``failed`` and ``uncertain`` player operations in the same transaction, so a job in flight ends
``not_connected`` at its hold. ``succeeded`` operations stay (history). The publication counters
are not touched: the binding guarantees a reconnect is the same player account.

``PLAYER_OPERATION_KINDS`` lists the ``external_operations`` kinds of "Send to player"; it is
empty until those writes exist (ADR 0034 section 5, PC-5 adds them here), and the statement then
deletes nothing.
"""

from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PLAYER_OPERATION_KINDS: tuple[str, ...] = ()
FORGOTTEN_STATES: Final = ("pending", "failed", "uncertain")

_FORGET = text(
    "DELETE FROM external_operations WHERE owner_id = :owner_id "
    "AND kind = ANY(CAST(:kinds AS text[])) AND state = ANY(CAST(:states AS text[]))"
)


async def forget_player_operations(db: AsyncSession, owner_id: UUID) -> None:
    """In the caller's transaction, after the connection row lock."""
    await db.execute(
        _FORGET,
        {
            "owner_id": owner_id,
            "kinds": list(PLAYER_OPERATION_KINDS),
            "states": list(FORGOTTEN_STATES),
        },
    )
