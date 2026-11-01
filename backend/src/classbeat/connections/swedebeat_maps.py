"""What classbeat keeps about a user's Swedebeat mixes (ADR 0016 section 3, A2): the table
``swedebeat_mix_maps`` (the user's own analysis of one of their mixes; no audio, nothing
Spotify-derived) and the MOWL links of Swedebeat mixes.

- ``analysed_mix_ids``: the mix ids the user has an analysis of, for the picker's "analysed before"
  badge. Ids only: never a name, a map or a playlist id.
- ``FORGET_MIX_MAPS``: every analysis of the user, run by ``repository._forget_mowl_account`` in
  the transaction that drops or replaces the MOWL connection (beside the user's links, which
  include the Swedebeat ones), so a reconnect as another MOWL account never reuses a map or a MOWL
  playlist id of the old one.

Every statement filters on ``owner_id`` itself as well as through row security.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session

_ANALYSED = text("SELECT mix_id FROM swedebeat_mix_maps WHERE owner_id = :owner_id")
FORGET_MIX_MAPS = text("DELETE FROM swedebeat_mix_maps WHERE owner_id = :owner_id")


async def analysed_mix_ids(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext
) -> frozenset[str]:
    async with user_session(factory, ctx) as db:
        rows = await db.execute(_ANALYSED, {"owner_id": ctx.user_id})
        return frozenset(str(mix_id) for (mix_id,) in rows)
