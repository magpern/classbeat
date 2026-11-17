"""What a succeeded ``spotify_create_playlist`` records (ADR 0008 section 2), in the transaction
that records it (whoever does: the worker, its reconciliation, the user's Check again on a final
row). Moved out of ``classbeat.playlists.create`` (CLAUDE.md rule 2, SR C7), which registers it.

- **pool**: ``spotify_pool.playlist_id``, set while the pool row is still the one the create was
  for and still has no playlist;
- **final**: the ``class_playlists`` row (``created``) of the operation's own class and revision.
  The final key is the job's (``<job_id>:final-create``) or a studio output's, bound to its
  approval (``out:...:spotify_create``, ADR 0036 section 6); ``purpose_fits`` reads either.

An operation that is not such a create, or whose context does not read, records nothing (a
warning with the kind only).
"""

import logging
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.jobs import JobFailure
from classbeat.operations import Operation
from classbeat.operations.kinds import SPOTIFY_CREATE_KIND, spotify_purpose
from classbeat.playlists import common

logger = logging.getLogger(__name__)

KIND: Final = SPOTIFY_CREATE_KIND


_CREATED = text(
    "SELECT external_id FROM external_operations WHERE owner_id = :owner_id AND kind = :kind "
    "AND idempotency_key = :key AND state = 'succeeded'"
)


async def created_id(db: AsyncSession, owner_id: UUID, key: str) -> str | None:
    """The playlist id the create of ``key`` returned; None unless that row ``succeeded``."""
    row = (await db.execute(_CREATED, {"owner_id": owner_id, "kind": KIND, "key": key})).first()
    return None if row is None else row.external_id


def purpose_fits(operation: Operation) -> bool:
    purpose = spotify_purpose(operation.kind, operation.idempotency_key)
    return purpose is not None and (purpose == "final") == (operation.class_id is not None)


_POOL_PLAYLIST = text(
    "UPDATE spotify_pool SET playlist_id = :playlist_id, updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :pool_id AND playlist_id IS NULL"
)
# The class playlist, from the operation row and the ``song_list`` revision **of the operation's
# own class** with the stored hash, so it can only ever name one owner's class, revision and
# operation. ``ON CONFLICT DO NOTHING``: idempotent (the live-playlist index).
_CLASS_PLAYLIST = text(
    "INSERT INTO class_playlists (owner_id, class_id, revision_id, playlist_id, spotify_user_id, "
    "name, state, create_operation_id) "
    "SELECT o.owner_id, o.class_id, r.id, :playlist_id, :spotify_user_id, :name, 'created', o.id "
    "FROM external_operations o JOIN class_revisions r ON r.owner_id = o.owner_id "
    "AND r.class_id = o.class_id AND r.id = :revision_id "
    "WHERE o.owner_id = :owner_id AND o.id = :operation_id AND o.kind = :kind "
    "AND o.state = 'succeeded' AND o.external_id = :playlist_id AND o.class_id = :class_id "
    "AND r.kind = 'song_list' AND r.content_hash = :content_hash "
    "ON CONFLICT DO NOTHING"
)


async def on_succeeded(db: AsyncSession, owner_id: UUID, operation: Operation) -> None:
    playlist_id = operation.external_id
    if (
        operation.kind != KIND
        or not purpose_fits(operation)
        or not isinstance(playlist_id, str)
        or common.SPOTIFY_ID.fullmatch(playlist_id) is None
    ):
        logger.warning("created playlist not recorded: not a create", extra={"kind": KIND})
        return
    context = operation.context
    try:
        if operation.class_id is None:
            await db.execute(
                _POOL_PLAYLIST,
                {
                    "owner_id": owner_id,
                    "pool_id": UUID(str(context["pool_id"])),
                    "playlist_id": playlist_id,
                },
            )
            return
        account = context["spotify_user_id"]
        name = common.check_name(context["name"])
        if not isinstance(account, str) or not 1 <= len(account) <= 100:
            raise ValueError("no Spotify user id")
        params = {
            "owner_id": owner_id,
            "operation_id": operation.id,
            "kind": KIND,
            "class_id": operation.class_id,
            "revision_id": UUID(str(context["revision_id"])),
            "content_hash": bytes.fromhex(str(context["content_hash"])),
            "playlist_id": playlist_id,
            "spotify_user_id": account,
            "name": name,
        }
    except (KeyError, ValueError, JobFailure):
        logger.warning("created playlist not recorded: unreadable operation", extra={"kind": KIND})
        return
    await db.execute(_CLASS_PLAYLIST, params)
