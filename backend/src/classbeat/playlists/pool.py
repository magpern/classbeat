"""The pool playlist's writes (ADR 0008 section 2): ensure it exists, then set it to the candidates.

``write_pool(run, keyring, opened, track_ids)``: the pool row is read; when it has no playlist
yet, a ``spotify_create_playlist`` (key ``<job_id>:pool-create``) makes one and its
``on_succeeded`` stores the id on the pool row; then a ``spotify_replace_items`` (key
``<job_id>:pool:<h16>``) writes exactly ``track_ids`` to **the id stored on the pool row**, never
one from anywhere else.

**The decide step** of both (``_guard``, inside the operation's fenced transaction, before its row
is written):

1. ``hold``: share-lock the Spotify connection the job opened, still that generation and Spotify
   user id (``not_connected`` otherwise). Every path that forgets the pool (a consent revoke, a
   Client ID change, Disconnect, an account change) takes that row's lock first, so none of them
   can interleave with the decide step.
2. Re-read ``spotify_pool``: the consent present, the same ``spotify_pool.id`` and Spotify user id
   (``consent_missing`` otherwise), and the same ``playlist_id`` the write is for (NULL for the
   create; ``pool_missing`` otherwise, retried, so the next attempt reads the pool again).
3. **Supersede** (the user is never asked to resolve a pool write): every other ``uncertain`` or
   orphaned ``pending`` pool row of the same kind (for a replace: of the same playlist) is marked
   ``failed`` with ``resolved_by = 'worker'``. A row is not touched, and the job requeues without
   an attempt (``operation_busy``), while it may still have a holder: another job of the user that
   is still ``queued`` or ``running`` (its next attempt would take the row over; ``admit``'s
   ``pool_busy`` keeps a second pick from running meanwhile, so this is defence in depth), an
   earlier attempt of this job still running under the row's ``lease_version``, or, for a
   replace, a row whose ``actionable_at`` has not passed (a stale call may still be sending
   requests). A superseded create that did happen leaves an empty private
   playlist nobody points at (the accepted risk of the threat model).

**``pool_missing``** (the owner check answered 404: the user deleted the playlist): in one
transaction the pool row's ``playlist_id`` and ``last_hash`` are cleared, conditional on it still
holding that playlist, and every pool operation row is deleted
(``classbeat.connections.spotify_data.pool_missing``), so the retry creates a new playlist under
a new row and ``ref`` and never reuses the deleted id. The category is retryable.
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import OpenConnection, hold, open_connection
from classbeat.connections.spotify_data import pool_missing
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations.kinds import SPOTIFY_CREATE_KIND, SPOTIFY_REPLACE_KIND
from classbeat.operations.requests import NOT_ACTIONABLE_SQL, Guard
from classbeat.playlists import common, create, replace

PROVIDER: Final = "spotify"


@dataclass(frozen=True, slots=True)
class Pool:
    """The user's consent row: its id, the pool playlist id (None until created) and the Spotify
    user id the consent was given for."""

    id: UUID
    playlist_id: str | None
    account: str


@dataclass(frozen=True, slots=True)
class PoolWrite:
    pool_id: UUID
    playlist_id: str
    created: bool
    count: int


_POOL = text("SELECT id, playlist_id, spotify_user_id FROM spotify_pool WHERE owner_id = :owner_id")
# The other pool rows of a kind that may still need resolving (``pending`` or ``uncertain``),
# locked: the supersede rule decides each.
_OTHERS = text(
    "SELECT id, state, job_id, lease_version, pending_at, "  # noqa: S608 - constant SQL
    f"{NOT_ACTIONABLE_SQL} AS waiting FROM external_operations "
    "WHERE owner_id = :owner_id AND kind = :kind AND class_id IS NULL "
    "AND idempotency_key <> :own_key AND state IN ('pending', 'uncertain') "
    "AND (CAST(:playlist_id AS text) IS NULL "
    "OR request_context ->> 'playlist_id' = CAST(:playlist_id AS text)) "
    "ORDER BY created_at FOR UPDATE"
)
_HOLDER_RUNNING = text(
    "SELECT 1 FROM jobs WHERE id = :job_id AND owner_id = :owner_id AND state = 'running' "
    "AND lease_version = :lease_version"
)
_JOB_OPEN = text(
    "SELECT 1 FROM jobs WHERE id = :job_id AND owner_id = :owner_id "
    "AND state IN ('queued', 'running')"
)
_SUPERSEDE = text(
    "UPDATE external_operations SET state = 'failed', resolved_by = 'worker', "
    "resolved_at = now(), updated_at = now() "
    "WHERE id = :id AND owner_id = :owner_id AND state = :state "
    "AND pending_at IS NOT DISTINCT FROM :pending_at RETURNING id"
)


async def read_pool(db: AsyncSession, owner_id: UUID) -> Pool | None:
    row = (await db.execute(_POOL, {"owner_id": owner_id})).first()
    if row is None:
        return None
    return Pool(row.id, row.playlist_id, row.spotify_user_id)


async def supersede(
    db: AsyncSession,
    owner_id: UUID,
    job_id: UUID,
    kind: str,
    own_key: str,
    playlist_id: str | None,
) -> int:
    """The supersede rule (module docstring) in the decide transaction of job ``job_id``; how
    many rows it marked. ``playlist_id``: a replace supersedes only rows of that playlist; None
    for the create."""
    rows = (
        await db.execute(
            _OTHERS,
            {"owner_id": owner_id, "kind": kind, "own_key": own_key, "playlist_id": playlist_id},
        )
    ).all()
    marked = 0
    for row in rows:
        await _supersede_one(db, owner_id, job_id, kind, row)
        marked += 1
    return marked


async def _supersede_one(
    db: AsyncSession, owner_id: UUID, job_id: UUID, kind: str, row: Any
) -> None:
    if row.job_id is not None and row.job_id != job_id:
        other = await db.execute(_JOB_OPEN, {"job_id": row.job_id, "owner_id": owner_id})
        if other.first() is not None:
            raise JobFailure("operation_busy")  # another pick may still act on it
    elif row.job_id is not None:
        holder = await db.execute(
            _HOLDER_RUNNING,
            {"job_id": row.job_id, "owner_id": owner_id, "lease_version": row.lease_version},
        )
        if holder.first() is not None:
            raise JobFailure("operation_busy")  # its call may be in flight
    if kind == SPOTIFY_REPLACE_KIND and row.waiting:
        raise JobFailure("operation_busy")  # a stale call may still be sending requests
    marked = await db.execute(
        _SUPERSEDE,
        {"id": row.id, "owner_id": owner_id, "state": row.state, "pending_at": row.pending_at},
    )
    if marked.first() is None:
        raise JobFailure("operation_busy")  # it moved meanwhile: decide again later


def _guard(
    run: JobRun, opened: OpenConnection, pool: Pool, kind: str, key: str, playlist_id: str | None
) -> Guard:
    async def guard(db: AsyncSession) -> bool:
        if not await hold(db, run.ctx, opened):
            return False
        found = await read_pool(db, run.lease.owner_id)
        if found is None or found.id != pool.id or found.account != opened.account:
            raise JobFailure("consent_missing")
        if found.playlist_id != playlist_id:
            raise JobFailure("pool_missing")  # it changed meanwhile: the retry reads it again
        await supersede(db, run.lease.owner_id, run.lease.job_id, kind, key, playlist_id)
        return True

    return guard


async def open_spotify(run: JobRun, keyring: KeyRing, connection_id: UUID) -> OpenConnection:
    """The job's Spotify connection: the context's and the input's, connected, with an account."""
    if run.ctx.connection(PROVIDER) != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, PROVIDER)
    if opened.id != connection_id or opened.account is None:
        raise JobFailure("not_connected")
    return opened


async def current_pool(run: JobRun, opened: OpenConnection) -> Pool:
    async with run.fenced() as fs:
        pool = await read_pool(fs.db, run.lease.owner_id)
    if pool is None or pool.account != opened.account:
        raise JobFailure("consent_missing")
    return pool


async def create_pool(run: JobRun, keyring: KeyRing, opened: OpenConnection, pool: Pool) -> str:
    """The pool playlist, created under this job's own key and a new ``ref``."""
    key = create.pool_key(run.lease.job_id)
    guard = _guard(run, opened, pool, SPOTIFY_CREATE_KIND, key, None)
    request = create.pool_request(run.lease.job_id, pool.id, guard)
    return await create.create(run, keyring, opened, request, common.POOL_NAME)


async def replace_pool(
    run: JobRun,
    keyring: KeyRing,
    opened: OpenConnection,
    pool: Pool,
    playlist_id: str,
    track_ids: tuple[str, ...],
) -> None:
    key = replace.pool_key(run.lease.job_id, common.ids_hash(track_ids))
    guard = _guard(run, opened, pool, SPOTIFY_REPLACE_KIND, key, playlist_id)
    request = replace.replace_request(key, "pool", playlist_id, track_ids, guard, pool_id=pool.id)
    try:
        await replace.replace(run, keyring, opened, request, track_ids)
    except JobFailure as failure:
        if failure.category == "pool_missing":
            async with run.fenced() as fs:
                await pool_missing(fs.db, run.lease.owner_id, pool.id, playlist_id)
        raise


async def write_pool(
    run: JobRun, keyring: KeyRing, opened: OpenConnection, track_ids: tuple[str, ...] | list[str]
) -> PoolWrite:
    """Ensure the pool playlist and set it to exactly ``track_ids`` (read back and compared)."""
    ids = common.check_track_ids(track_ids)
    pool = await current_pool(run, opened)
    created = False
    playlist_id = pool.playlist_id
    if playlist_id is None:
        playlist_id = await create_pool(run, keyring, opened, pool)
        pool = Pool(pool.id, playlist_id, pool.account)
        created = True
    await replace_pool(run, keyring, opened, pool, playlist_id, ids)
    return PoolWrite(pool.id, playlist_id, created, len(ids))
