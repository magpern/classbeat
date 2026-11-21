"""What classbeat keeps about a user's Spotify account, and how it is forgotten (ADR 0008 sections
2 and 8, ADR 0009 sections 7 and 9).

Every function runs inside the caller's transaction (a ``user_session`` of the owner, after the
caller has taken the Spotify connection row lock where there is one) and only there, so the
deletion and whatever caused it commit together or not at all. Every statement filters on
``owner_id`` itself as well as through row security. Nothing here calls Spotify: the playlists in
the user's Spotify are the user's own and are never deleted (ADR 0008 section 8).

- ``forget_pool``: the user's **pool** operation rows (``spotify_create_playlist`` and
  ``spotify_replace_items`` with ``class_id IS NULL``), any state. Never the consent row.
- ``revoke_pool``: the consent row and ``forget_pool`` (a consent revoke, a Client ID change).
- ``pool_missing``: the pool's playlist id and hash cleared, only while the row still holds the
  playlist that was found gone, and then ``forget_pool``.
- ``forget_spotify_account``: every Spotify operation row (pool and final), the consent row, and
  every live ``class_playlists`` row detached (history, never reused). Disconnect and an account
  change; the caller deletes or rewrites the connection row itself.
- ``forget_replaced_spotify``: a connect that rewrites an existing row decides, from the account
  and the Client ID it replaces, between ``forget_spotify_account``, ``revoke_pool`` and nothing.

The operations go first, as in the MOWL forget of ``classbeat.connections.repository``: a worker
recording an outcome locks the operation row and then writes ``spotify_pool`` or
``class_playlists``, so the same order here cannot deadlock with it, and an outcome recorded after
this finds its row gone.
"""

from collections.abc import Mapping
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections.errors import ConnectionFailure

# The external_operations.kind values of migration 0006 that write to Spotify.
SPOTIFY_OPERATION_KINDS: Final = ("spotify_create_playlist", "spotify_replace_items")
_KINDS_SQL: Final = ", ".join(f"'{kind}'" for kind in SPOTIFY_OPERATION_KINDS)

_FORGET_POOL_OPERATIONS = text(
    f"DELETE FROM external_operations WHERE owner_id = :owner_id AND kind IN ({_KINDS_SQL}) "  # noqa: S608
    "AND class_id IS NULL"
)
_FORGET_ALL_OPERATIONS = text(
    f"DELETE FROM external_operations WHERE owner_id = :owner_id AND kind IN ({_KINDS_SQL})"  # noqa: S608
)
_DELETE_POOL = text("DELETE FROM spotify_pool WHERE owner_id = :owner_id")
_CLEAR_POOL_PLAYLIST = text(
    "UPDATE spotify_pool SET playlist_id = NULL, last_hash = NULL, last_written_at = NULL, "
    "updated_at = now() WHERE owner_id = :owner_id AND id = :pool_id "
    "AND playlist_id = :playlist_id RETURNING id"
)
# ADR 0036 section 9 (SR C8): every revision's sync state names that Spotify account's playlists.
_FORGET_SYNCS = text("DELETE FROM spotify_syncs WHERE owner_id = :owner_id")
_DETACH_PLAYLISTS = text(
    "UPDATE class_playlists SET detached_at = now(), updated_at = now() "
    "WHERE owner_id = :owner_id AND detached_at IS NULL"
)


async def forget_pool(db: AsyncSession, owner_id: UUID) -> None:
    """Delete the user's pool operation rows in the caller's transaction (ADR 0008 section 2)."""
    await db.execute(_FORGET_POOL_OPERATIONS, {"owner_id": owner_id})


async def revoke_pool(db: AsyncSession, owner_id: UUID) -> None:
    """The consent is gone (a revoke, or a Client ID change): its operation rows, then the row."""
    await forget_pool(db, owner_id)
    await db.execute(_DELETE_POOL, {"owner_id": owner_id})


async def pool_missing(db: AsyncSession, owner_id: UUID, pool_id: UUID, playlist_id: str) -> bool:
    """The pool playlist ``playlist_id`` is gone from Spotify: clear it from the pool row and
    forget the pool operation rows, so the retry creates anew under a new key and ``ref``.

    Conditional on the row still holding that playlist: False (nothing changed, no operation row
    deleted) when the consent was revoked or the pool was set up again meanwhile, whose rows must
    stay. The operation rows are deleted first (the module's lock order) in a savepoint that is
    rolled back when the pool row no longer matches."""
    savepoint = await db.begin_nested()
    await forget_pool(db, owner_id)
    found = await db.execute(
        _CLEAR_POOL_PLAYLIST,
        {"owner_id": owner_id, "pool_id": pool_id, "playlist_id": playlist_id},
    )
    if found.first() is None:
        await savepoint.rollback()
        return False
    await savepoint.commit()
    return True


async def forget_spotify_account(db: AsyncSession, owner_id: UUID) -> None:
    """Disconnect and an account change (ADR 0009 section 7): every Spotify operation row, the
    consent row, every live class playlist detached, and every revision's sync state (SR C8). The
    connection row is the caller's."""
    await db.execute(_FORGET_ALL_OPERATIONS, {"owner_id": owner_id})
    await db.execute(_DELETE_POOL, {"owner_id": owner_id})
    await db.execute(_DETACH_PLAYLISTS, {"owner_id": owner_id})
    await db.execute(_FORGET_SYNCS, {"owner_id": owner_id})


async def forget_replaced_spotify(
    db: AsyncSession,
    owner_id: UUID,
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    confirm_account_change: bool,
) -> None:
    """ADR 0009 section 7, after the row lock: another Spotify account forgets everything kept
    about the old one, and only with ``confirm_account_change`` (else ``account_changed``, the
    transaction rolls back and nothing is written); the same account under another Client ID
    forgets the pool (the consent is asked again); a reconnect of the same account and app keeps
    both. A value that is missing on either side counts as different (the safe reading)."""

    def same(key: str) -> bool:
        value = old.get(key)
        return isinstance(value, str) and bool(value) and value == new.get(key)

    if not same("spotify_user_id"):
        if not confirm_account_change:
            raise ConnectionFailure("account_changed", not_applied=True)
        await forget_spotify_account(db, owner_id)
    elif not same("client_id"):
        await revoke_pool(db, owner_id)
