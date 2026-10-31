"""A class's final playlist (ADR 0008 sections 2 and 4): create it, then fill it with the approved
songs. The job kinds of phase 2 (SJ2) call these; they only write.

- ``create_final_playlist``: a ``spotify_create_playlist`` with key ``<job_id>:final-create`` and
  the class; its ``on_succeeded`` inserts the ``class_playlists`` row (``created``) for the
  approved ``song_list`` revision. Returns that row's id and the playlist id.
- ``fill_final_playlist``: a ``spotify_replace_items`` with key ``<job_id>:final-fill`` and the
  class, to the playlist of the user's own **live** ``class_playlists`` row of that class (named
  by the row's id, never by a playlist id from input), connected as the same Spotify account;
  its ``on_succeeded`` marks the row ``verified``.

The decide step of both share-locks the Spotify connection the job opened (same generation and
Spotify user id, ``not_connected`` otherwise); the fill also re-reads the class playlist row there
(still live, the same playlist and account). Every failure keeps what exists; an uncertain final
write is the user's to resolve (Check again / Do it anyway).
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import OpenConnection, hold
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations.requests import Guard
from classbeat.playlists import common, create, replace


@dataclass(frozen=True, slots=True)
class ClassPlaylist:
    id: UUID
    playlist_id: str


_CLASS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_BY_OPERATION = text(
    "SELECT id, playlist_id FROM class_playlists WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND playlist_id = :playlist_id AND detached_at IS NULL"
)
_LIVE = text(
    "SELECT playlist_id, spotify_user_id FROM class_playlists WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND id = :id AND detached_at IS NULL"
)
NO_ROW: Final = "invalid_input"


def _create_guard(run: JobRun, opened: OpenConnection, class_id: UUID) -> Guard:
    async def guard(db: AsyncSession) -> bool:
        if not await hold(db, run.ctx, opened):
            return False
        found = await db.execute(_CLASS, {"owner_id": run.lease.owner_id, "class_id": class_id})
        if found.first() is None:
            raise JobFailure("invalid_input")
        return True

    return guard


async def create_final_playlist(
    run: JobRun,
    keyring: KeyRing,
    opened: OpenConnection,
    *,
    class_id: UUID,
    revision_id: UUID,
    content_hash: bytes,
    name: str,
) -> ClassPlaylist:
    """The class's new final playlist and its ``class_playlists`` row."""
    if opened.account is None:
        raise JobFailure("not_connected")
    request = create.final_request(
        run.lease.owner_id,
        run.lease.job_id,
        class_id,
        revision_id,
        content_hash,
        name,
        opened.account,
        _create_guard(run, opened, class_id),
    )
    playlist_id = await create.create(run, keyring, opened, request, common.check_name(name))
    async with run.fenced() as fs:
        row = (
            await fs.db.execute(
                _BY_OPERATION,
                {"owner_id": run.lease.owner_id, "class_id": class_id, "playlist_id": playlist_id},
            )
        ).first()
    if row is None:
        raise JobFailure("invalid_input")  # the revision is not this class's approved list
    return ClassPlaylist(row.id, row.playlist_id)


async def _live(db: AsyncSession, owner_id: UUID, class_id: UUID, row_id: UUID) -> tuple[str, str]:
    row = (
        await db.execute(_LIVE, {"owner_id": owner_id, "class_id": class_id, "id": row_id})
    ).first()
    if row is None:
        raise JobFailure(NO_ROW)
    return row.playlist_id, row.spotify_user_id


async def fill_final_playlist(
    run: JobRun,
    keyring: KeyRing,
    opened: OpenConnection,
    *,
    class_id: UUID,
    class_playlist_id: UUID,
    track_ids: tuple[str, ...] | list[str],
) -> None:
    """Set the class playlist to exactly ``track_ids`` and verify it by reading it back."""
    ids = common.check_track_ids(track_ids)
    owner_id = run.lease.owner_id
    async with run.fenced() as fs:
        playlist_id, account = await _live(fs.db, owner_id, class_id, class_playlist_id)
    if account != opened.account:
        raise JobFailure("account_changed")
    playlist_id = common.check_playlist_id(playlist_id)

    async def guard(db: AsyncSession) -> bool:
        if not await hold(db, run.ctx, opened):
            return False
        still, still_account = await _live(db, owner_id, class_id, class_playlist_id)
        return still == playlist_id and still_account == opened.account

    request = replace.replace_request(
        replace.final_key(run.lease.job_id), "final", playlist_id, ids, guard, class_id=class_id
    )
    await replace.replace(run, keyring, opened, request, ids)
