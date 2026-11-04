"""The user's "I use Swedebeat" preference for tests (#525): default off, so a Swedebeat test turns
it on for its user, as the user would on the account page (the same write the route makes)."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.connections.preferences import set_uses_swedebeat
from ctx import make_context


async def use_swedebeat(
    factory: async_sessionmaker[AsyncSession], user_id: UUID, on: bool = True
) -> None:
    assert await set_uses_swedebeat(factory, make_context(user_id), on) is on
