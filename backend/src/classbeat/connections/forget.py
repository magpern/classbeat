"""What classbeat keeps about a provider account, forgotten with its connection (ADR 0006 section 3,
ADR 0007 section 9, ADR 0009 section 7, ADR 0034 section 3, ADR 0036 section 4.1). Each runs in
the caller's transaction, after the connection row lock (``repository.disconnect``, ``save``,
``delete_all_in``), and every statement filters on ``owner_id`` itself as well as through row
security.

- **MOWL** (``forget_mowl_account``, Disconnect and a reconnect in place): every MOWL operation
  (attach, save and update, a studio mix's attach, in any state) is deleted with the studio
  classes' MOWL outputs (``class_outputs``), and every live saved workout is detached
  (kept as history, out of the live-program index, never re-attached), the playlist links go
  (Spotify and Swedebeat), and so do the mix analyses and the hand-offs, which belong to that
  MOWL account. The operations go first: a worker recording an attach or a save locks the
  operation row and then writes the link or the saved row, so taking the same order here cannot
  deadlock with it, and a save recorded after this finds its row gone (``not_connected``).
- **SwedeBeat** (``forget_swedebeat_account``, Disconnect and a keys wipe): the studio's track
  analyses (``swedebeat_track_maps``). A worker stores one only while it holds the connection
  (``repository.hold``), so a store after this finds the connection gone and writes nothing. A
  reconnect in place keeps them: a bearer expires daily, and the catalogue is the same. The
  account digest a publish recorded in its operation's context (``account``, #1117) is removed
  too: the operation stays, and without it a later Check again stays ``connection_changed``.
  The audition's cached tracks and clips go too (``FORGET_PREVIEW``), their files with the sweep.
- **Spotify** and **player** are their own modules' (``spotify_data``, ``player_data``).
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.player_data import forget_player_operations
from classbeat.connections.spotify_data import forget_spotify_account
from classbeat.connections.swedebeat_maps import FORGET_MIX_MAPS, FORGET_TRACK_MAPS
from classbeat.context import UserContext

FORGET_MOWL: Final = (
    text(
        "DELETE FROM external_operations WHERE owner_id = :owner_id AND kind IN "
        "('mowl_attach_spotify_playlist', 'mowl_attach_swedebeat_mix', "
        "'mowl_save_workout', 'mowl_update_workout', 'mowl_attach_studio_mix')"
    ),
    # ADR 0036 section 6 (SR C7): a studio class's MOWL output belongs to that MOWL account too.
    text("DELETE FROM class_outputs WHERE owner_id = :owner_id AND output = 'mowl'"),
    text(
        "UPDATE saved_workouts SET detached_at = now(), updated_at = now() "
        "WHERE owner_id = :owner_id AND detached_at IS NULL"
    ),
    text("DELETE FROM mowl_playlist_links WHERE owner_id = :owner_id"),  # Spotify and Swedebeat
    FORGET_MIX_MAPS,  # ADR 0016 section 3: the analysis belongs to the MOWL account it was made in
    # ADR 0016 section 7: the hand-off belongs to that account too. Inlined so connections does not
    # import the hand-off package (that package imports connections).
    text("DELETE FROM swedebeat_handoffs WHERE owner_id = :owner_id"),
)


async def forget_mowl_account(db: AsyncSession, ctx: UserContext) -> None:
    for statement in FORGET_MOWL:
        await db.execute(statement, {"owner_id": ctx.user_id})


FORGET_PUBLISH_ACCOUNT: Final = text(
    "UPDATE external_operations SET request_context = request_context - 'account' "
    "WHERE owner_id = :owner_id AND kind = 'swedebeat_publish_playlist' "
    "AND request_context ? 'account'"
)


# ADR 0042 section 6.5 (#1394): the audition's cached catalogue tracks and the clips rendered from
# them are that account's audio too. The rows go here; their files are no row's, and the hourly
# sweep removes them (``preview.purge``).
FORGET_PREVIEW: Final = (
    text("DELETE FROM preview_clips WHERE owner_id = :owner_id"),
    text("DELETE FROM preview_sources WHERE owner_id = :owner_id"),
)


async def forget_swedebeat_account(db: AsyncSession, ctx: UserContext) -> None:
    for statement in FORGET_PREVIEW:
        await db.execute(statement, {"owner_id": ctx.user_id})
    await db.execute(FORGET_TRACK_MAPS, {"owner_id": ctx.user_id})
    await db.execute(FORGET_PUBLISH_ACCOUNT, {"owner_id": ctx.user_id})


async def forget_account(db: AsyncSession, ctx: UserContext, provider: str) -> None:
    """What a Disconnect of ``provider`` forgets beside the row."""
    if provider == "mowl":
        await forget_mowl_account(db, ctx)
    elif provider == "spotify":
        await forget_spotify_account(db, ctx.user_id)
    elif provider == "player":
        await forget_player_operations(db, ctx.user_id)
    elif provider == "swedebeat":
        await forget_swedebeat_account(db, ctx)


async def forget_every_account(db: AsyncSession, ctx: UserContext) -> None:
    """A keys wipe: what every provider's Disconnect forgets."""
    await forget_mowl_account(db, ctx)
    await forget_spotify_account(db, ctx.user_id)
    await forget_player_operations(db, ctx.user_id)
    await forget_swedebeat_account(db, ctx)
