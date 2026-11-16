"""The Spotify sync state of a revision (ADR 0036 section 9, SR C8): ``spotify_syncs``.

A studio revision's content follows into a Spotify playlist: a ``song_list`` into the user's
working copy (the pool playlist, ``target = 'working_copy'``) and an approved draft into its final
playlist (``target = 'final'``). Each ``(class, revision, target)`` has one row whose ``state`` is
``pending``, ``synced`` or ``failed`` (with the failure's category in ``detail``).

1. **Intend** (``intend``, in the caller's fenced transaction): the row is written ``pending``
   with a new ``seq``. The newest ``seq`` of a playlist (``lock_key``) is its **intended
   revision**; "Sync again" intends the same row again.
2. **Write** (``run_sync``): the caller's write (``playlists.write_pool`` or
   ``fill_final_playlist``) holds the playlist's lock (``playlists.lock``) for the whole write and
   runs ``recheck`` in the decide step of **each** external write it makes, right before the
   write intent is committed: a row that is no longer the intended one raises ``Superseded``, so
   a stale write hands over to the newer revision's sync without writing. The write replaces the
   whole list and reads it back; only an equal read-back succeeds.
3. **Settle**: ``synced`` after the read-back matched; ``failed`` with ``superseded`` when a newer
   revision took the playlist over; ``failed`` with the write's category otherwise. ``lease_lost``
   always propagates (nothing can be recorded without the fence), and so does ``operation_busy``
   when the caller can be requeued cheaply (``requeue_busy``). A failure never ends the caller's
   job: the studio's SwedeBeat outputs and the list's approval go on, and the user sees the state
   with "Sync again" (``classes.studio_sync``). For a final playlist the failure is also the studio
   output's (its own state and retry, ADR 0036 section 6), and its sync uses the output's own fill
   key, so "Sync again" settles that operation, and the output with it, instead of adding a
   second one.

4. **Interrupted**: a row left ``pending`` with no job that could still write it (the "Sync
   again" job cancelled, or ended by its attempts, a crash or a missing runtime; a pick or an
   output job that ended between intending and settling) is shown ``failed`` with
   ``interrupted`` (``shown``) and "Sync again" takes it like a failed one. Derived, not
   written: every way a job ends (the user's cancel, the worker's terminal transitions, the
   expiry sweep) is covered without a hook in each. A job "could still write it" while it is
   ``queued`` or ``running``: a ``spotify_sync`` job of this revision and target, or any job of
   the class (a pick, a list action, an output job).

For a final playlist the recheck also waits (``operation_busy``) while another replace of that
playlist is ``pending`` or ``uncertain`` inside its bound: a write the lock no longer covers (its
worker lost the lock's connection) can then not interleave with this one.

Nothing here logs a playlist id or a track id.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.jobs import JobFailure, JobRun
from classbeat.operations.kinds import SPOTIFY_REPLACE_KIND
from classbeat.operations.requests import NOT_ACTIONABLE_SQL, Guard
from classbeat.playlists import common
from classbeat.playlists.lock import WORKING_COPY

Target = Literal["working_copy", "final"]
TARGETS: Final[tuple[Target, ...]] = ("working_copy", "final")
SUPERSEDED: Final = "superseded"
INTERRUPTED: Final = "interrupted"


class Superseded(Exception):
    """A newer revision is the playlist's intended one: this write hands over without writing."""


@dataclass(frozen=True, slots=True)
class Sync:
    id: UUID
    class_id: UUID
    revision_id: UUID
    target: Target
    lock_key: str
    class_playlist_id: UUID | None
    fill_key: str | None
    track_ids: tuple[str, ...]
    state: str
    detail: str | None


def lock_key(target: Target, class_playlist_id: UUID | None) -> str:
    if target == "working_copy":
        return WORKING_COPY
    if class_playlist_id is None:
        raise JobFailure("internal")
    return f"final:{class_playlist_id}"


_COLUMNS = (
    "id, class_id, revision_id, target, lock_key, class_playlist_id, fill_key, track_ids, state, "
    "detail"
)
_INTEND = text(
    "INSERT INTO spotify_syncs (owner_id, class_id, revision_id, target, class_playlist_id, "  # noqa: S608
    "fill_key, lock_key, track_ids, state) VALUES (:owner_id, :class_id, :revision_id, :target, "
    ":class_playlist_id, :fill_key, :lock_key, CAST(:track_ids AS jsonb), 'pending') "
    "ON CONFLICT (owner_id, class_id, revision_id, target) DO UPDATE SET state = 'pending', "
    "detail = NULL, class_playlist_id = EXCLUDED.class_playlist_id, "
    "fill_key = EXCLUDED.fill_key, "
    "lock_key = EXCLUDED.lock_key, track_ids = EXCLUDED.track_ids, "
    "seq = nextval(pg_get_serial_sequence('public.spotify_syncs', 'seq')), updated_at = now() "
    f"RETURNING {_COLUMNS}"
)
_AGAIN = text(
    "UPDATE spotify_syncs SET state = 'pending', detail = NULL, updated_at = now(), "
    "seq = nextval(pg_get_serial_sequence('public.spotify_syncs', 'seq')) "
    "WHERE owner_id = :owner_id AND id = :id AND state IN ('failed', 'pending') RETURNING id"
)
# A job that could still write a row (see "Interrupted" above).
_WRITER = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND state IN ('queued', 'running') AND "
    "(class_id = :class_id OR (kind = 'spotify_sync' AND class_id IS NULL "
    "AND input ->> 'source_class_id' = :class_text AND input ->> 'revision_id' = :revision_text "
    "AND input ->> 'target' = :target)) LIMIT 1"
)
_LOAD = text(
    f"SELECT {_COLUMNS} FROM spotify_syncs WHERE owner_id = :owner_id "  # noqa: S608 - constants
    "AND class_id = :class_id AND revision_id = :revision_id AND target = :target"
)
_LOAD_FOR_UPDATE = text(f"{_LOAD.text} FOR UPDATE")
_INTENDED = text(
    "SELECT id FROM spotify_syncs WHERE owner_id = :owner_id AND lock_key = :lock_key "
    "ORDER BY seq DESC LIMIT 1"
)
_SETTLE = text(
    "UPDATE spotify_syncs SET state = :state, detail = :detail, updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :id AND state = 'pending'"
)
# Another replace of this playlist that may still be sending requests (ADR 0007 section 8).
_IN_FLIGHT = text(
    "SELECT 1 FROM external_operations WHERE owner_id = :owner_id AND kind = :kind "  # noqa: S608
    "AND request_context ->> 'playlist_id' = :playlist_id AND idempotency_key <> :own_key "
    f"AND (state = 'pending' OR (state = 'uncertain' AND {NOT_ACTIONABLE_SQL})) LIMIT 1"
)


def _sync(row: Any) -> Sync:
    return Sync(
        row.id,
        row.class_id,
        row.revision_id,
        row.target,
        row.lock_key,
        row.class_playlist_id,
        row.fill_key,
        common.check_track_ids(row.track_ids),
        row.state,
        row.detail,
    )


async def intend(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    revision_id: UUID,
    target: Target,
    track_ids: Sequence[str],
    class_playlist_id: UUID | None = None,
    fill_key: str | None = None,
) -> Sync:
    """Make this revision the playlist's intended one, ``pending`` (step 1). A final playlist
    names its ``class_playlists`` row and its fill's operation key."""
    if (target == "final") != (class_playlist_id is not None and fill_key is not None):
        raise JobFailure("internal")
    ids = common.check_track_ids(track_ids)
    row = (
        await db.execute(
            _INTEND,
            {
                "owner_id": owner_id,
                "class_id": class_id,
                "revision_id": revision_id,
                "target": target,
                "class_playlist_id": class_playlist_id,
                "fill_key": fill_key,
                "lock_key": lock_key(target, class_playlist_id),
                "track_ids": json.dumps(list(ids)),
            },
        )
    ).one()
    return _sync(row)


async def has_writer(db: AsyncSession, owner_id: UUID, sync: Sync) -> bool:
    """A ``queued`` or ``running`` job could still write ``sync`` (see "Interrupted")."""
    params = {
        "owner_id": owner_id,
        "class_id": sync.class_id,
        "class_text": str(sync.class_id),
        "revision_text": str(sync.revision_id),
        "target": sync.target,
    }
    return (await db.execute(_WRITER, params)).first() is not None


async def shown(db: AsyncSession, owner_id: UUID, sync: Sync) -> Sync:
    """The row as the user sees it: ``pending`` with no writer is ``failed``/``interrupted``."""
    if sync.state == "pending" and not await has_writer(db, owner_id, sync):
        return Sync(
            sync.id,
            sync.class_id,
            sync.revision_id,
            sync.target,
            sync.lock_key,
            sync.class_playlist_id,
            sync.fill_key,
            sync.track_ids,
            "failed",
            INTERRUPTED,
        )
    return sync


async def again(db: AsyncSession, owner_id: UUID, sync_id: UUID) -> bool:
    """ "Sync again": the row (``failed`` as ``shown``, the caller checked) is intended again,
    ``pending``. False for a synced one."""
    return (await db.execute(_AGAIN, {"owner_id": owner_id, "id": sync_id})).first() is not None


async def load(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    revision_id: UUID,
    target: Target,
    *,
    lock: bool = False,
) -> Sync | None:
    params = {
        "owner_id": owner_id,
        "class_id": class_id,
        "revision_id": revision_id,
        "target": target,
    }
    row = (await db.execute(_LOAD_FOR_UPDATE if lock else _LOAD, params)).first()
    return None if row is None else _sync(row)


async def is_intended(db: AsyncSession, owner_id: UUID, sync: Sync) -> bool:
    found = await db.execute(_INTENDED, {"owner_id": owner_id, "lock_key": sync.lock_key})
    return found.scalar_one_or_none() == sync.id


def recheck(run: JobRun, sync: Sync) -> Guard:
    """The decide-step check of each write for ``sync`` (see the module docstring)."""
    owner_id = run.lease.owner_id

    async def guard(db: AsyncSession) -> bool:
        if not await is_intended(db, owner_id, sync):
            raise Superseded
        return True

    return guard


def final_recheck(run: JobRun, sync: Sync, playlist_id: str, own_key: str) -> Guard:
    """``recheck`` for a final playlist, also waiting out another replace still in flight."""
    first = recheck(run, sync)
    owner_id = run.lease.owner_id

    async def guard(db: AsyncSession) -> bool:
        await first(db)
        params = {
            "owner_id": owner_id,
            "kind": SPOTIFY_REPLACE_KIND,
            "playlist_id": playlist_id,
            "own_key": own_key,
        }
        if (await db.execute(_IN_FLIGHT, params)).first() is not None:
            raise JobFailure("operation_busy")
        return True

    return guard


async def _settle(run: JobRun, sync: Sync, state: str, detail: str | None) -> None:
    async with run.fenced() as fs:
        await fs.db.execute(
            _SETTLE,
            {"owner_id": run.lease.owner_id, "id": sync.id, "state": state, "detail": detail},
        )


Outcome = Literal["synced", "failed", "superseded"]


async def run_sync(
    run: JobRun,
    sync: Sync,
    write: Callable[[], Awaitable[object]],
    *,
    requeue_busy: bool,
    reraise: bool = False,
) -> Outcome:
    """Run ``write`` (which holds the lock and rechecks, step 2) and settle the row (step 3).
    ``reraise``: a failure is also the caller's (a studio output's own state, ADR 0036 section
    6); a hand-over never is."""
    try:
        await write()
    except Superseded:
        await _settle(run, sync, "failed", SUPERSEDED)
        return "superseded"
    except JobFailure as failure:
        if failure.category == "lease_lost":
            raise
        if requeue_busy and failure.category == "operation_busy":
            raise
        await _settle(run, sync, "failed", failure.category)
        if reraise:
            raise
        return "failed"
    await _settle(run, sync, "synced", None)
    return "synced"


def view(sync: Sync | None) -> dict[str, Any] | None:
    """What the page shows of a row: its state and a failure's category. No id, no track."""
    if sync is None:
        return None
    return {"state": sync.state, "detail": sync.detail}
