"""What the audition keeps, and when it goes (ADR 0042 section 6.5).

Every function runs in the caller's transaction (a job's fenced session, or the hourly purge's
per-user session, ``auth.purge``) and returns the **files** of the rows it deleted; the caller
removes them once that transaction committed (``storage.remove``), so a rolled-back delete never
loses a file its row still names. Every statement filters on ``owner_id`` as well as through row
security.

- ``cap``: the owner's clips and sources over ``storage.owner_max_bytes()`` together, the least
  recently used first (the newest is kept while it fits).
- ``purge_owner``: listening sessions a day old (``session_policy.KEEP_SESSIONS``), clips and
  sources idle for 14 days (``IDLE_DAYS``), then ``cap``, then the **orphan sweep** of the
  owner's directory: a row whose file is gone is deleted; a file no row names (a crash between
  ``storage.place`` and its insert, or a delete whose file removal failed) is returned once it is
  older than ``ORPHAN_GRACE``, so a file placed for an insert still in flight is never taken.
- ``stray_owner_dirs``: directories under the root that are no user's (an account deleted while
  its removal failed; the admin delete), older than the grace.

With the class (``ON DELETE CASCADE``) and with the account, the rows go in the deleting
transaction; their files are removed after it (``classes.removal``, ``account.erase``) or, failing
that, by the sweep.
"""

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.preview import session_policy, storage

ORPHAN_GRACE: Final = 3600.0  # seconds

_OVER_CAP = text(
    "WITH files AS ("
    " SELECT 'clips' AS kind, id, bytes, last_used_at FROM preview_clips"
    " WHERE owner_id = :owner_id"
    " UNION ALL"
    " SELECT 'sources', id, bytes, last_used_at FROM preview_sources WHERE owner_id = :owner_id"
    "), ranked AS ("
    " SELECT kind, id, sum(bytes) OVER (ORDER BY last_used_at DESC, id DESC) AS running"
    " FROM files"
    ") SELECT kind, id FROM ranked WHERE running > :cap"
)
_SESSIONS = text(
    "DELETE FROM preview_sessions WHERE owner_id = :owner_id "
    "AND created_at <= now() - make_interval(days => :days)"
)
_IDLE = {
    kind: text(
        f"DELETE FROM preview_{kind} WHERE owner_id = :owner_id "  # noqa: S608 - constants only
        "AND last_used_at <= now() - make_interval(days => :days) RETURNING id"
    )
    for kind in storage.KINDS
}
_BY_ID = {
    kind: text(
        f"DELETE FROM preview_{kind} WHERE owner_id = :owner_id AND id IN :ids"  # noqa: S608
    ).bindparams(bindparam("ids", expanding=True))
    for kind in storage.KINDS
}
_IDS = {
    kind: text(f"SELECT id FROM preview_{kind} WHERE owner_id = :owner_id")  # noqa: S608
    for kind in storage.KINDS
}


@dataclass
class Purged:
    """Rows deleted per table, and the files to remove once the transaction committed."""

    counts: dict[str, int] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)

    def add(self, name: str, count: int) -> None:
        self.counts[name] = self.counts.get(name, 0) + count


def _paths(root: Path, owner_id: UUID, kind: storage.Kind, ids: Iterable[UUID]) -> list[Path]:
    return [storage.path_of(root, owner_id, kind, i) for i in ids]


async def _delete(
    db: AsyncSession, root: Path, owner_id: UUID, kind: storage.Kind, ids: list[UUID]
) -> list[Path]:
    if not ids:
        return []
    await db.execute(_BY_ID[kind], {"owner_id": owner_id, "ids": ids})
    return _paths(root, owner_id, kind, ids)


async def cap(db: AsyncSession, root: Path, owner_id: UUID) -> list[Path]:
    """Delete the owner's least recently used rows over the size cap; their files."""
    params = {"owner_id": owner_id, "cap": storage.owner_max_bytes()}
    over = (await db.execute(_OVER_CAP, params)).all()
    files: list[Path] = []
    for kind in storage.KINDS:
        files += await _delete(db, root, owner_id, kind, [r.id for r in over if r.kind == kind])
    return files


def _on_disk(root: Path, owner_id: UUID, kind: storage.Kind) -> tuple[dict[UUID, Path], list[Path]]:
    """The files of the owner's ``kind`` directory: by row id, and the rest (temporary names)."""
    directory = storage.owner_dir(root, owner_id) / kind
    named: dict[UUID, Path] = {}
    other: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return named, other
    for entry in entries:
        try:
            named[UUID(entry.name.removesuffix(storage.SUFFIX))] = entry
        except ValueError:
            other.append(entry)
    return named, other


def _old(path: Path, now: float) -> bool:
    try:
        return now - path.lstat().st_mtime > ORPHAN_GRACE
    except OSError:
        return False


async def _sweep(db: AsyncSession, root: Path, owner_id: UUID, out: Purged) -> None:
    now = time.time()
    for kind in storage.KINDS:
        rows = set((await db.execute(_IDS[kind], {"owner_id": owner_id})).scalars())
        named, other = _on_disk(root, owner_id, kind)
        missing = [i for i in rows if i not in named]
        await _delete(db, root, owner_id, kind, missing)
        out.add(f"preview_{kind}_missing", len(missing))
        unnamed = [p for i, p in named.items() if i not in rows] + other
        orphans = [p for p in unnamed if _old(p, now)]
        out.files += orphans
        out.add(f"preview_{kind}_orphans", len(orphans))


async def purge_owner(db: AsyncSession, root: Path | None, owner_id: UUID) -> Purged:
    """The hourly purge of one owner (see the module docstring)."""
    out = Purged()
    days = {"owner_id": owner_id, "days": session_policy.KEEP_SESSIONS.days}
    gone = await db.execute(_SESSIONS, days)
    out.add("preview_sessions", int(gone.rowcount or 0))  # pyright: ignore[reportAttributeAccessIssue]
    if root is None:
        return out  # the audition is off: rows age out, there are no files to look at
    idle = {"owner_id": owner_id, "days": session_policy.IDLE_DAYS}
    for kind in storage.KINDS:
        ids = list((await db.execute(_IDLE[kind], idle)).scalars())
        out.files += _paths(root, owner_id, kind, ids)
        out.add(f"preview_{kind}", len(ids))
    evicted = await cap(db, root, owner_id)
    out.files += evicted
    out.add("preview_evicted", len(evicted))
    await _sweep(db, root, owner_id, out)
    return out


def stray_owner_dirs(root: Path | None, known: Iterable[UUID]) -> list[Path]:
    """Directories under the root that are no known user's, older than the grace."""
    if root is None:
        return []
    owners = {str(k) for k in known}
    now = time.time()
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    return [e for e in entries if e.name not in owners and _old(e, now)]
