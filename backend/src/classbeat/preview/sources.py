"""The audition's per-owner source-track cache (ADR 0042 section 6.5): ``preview_sources``, one
file per ``(owner_id, class_id, reference)`` on the preview volume, under the owner's own path.

``gather`` puts each **distinct** track of a ``preview_render`` plan into the render's private
directory and returns its path there:

- **A hit** is a row of this class and reference whose SHA-256 is the analysed bytes' and whose
  file copies into the directory with exactly that SHA-256 (``storage.copy_checked``, so a file
  changed on the volume is never rendered). Its ``last_used_at`` is touched.
- **A miss** (no row, another SHA-256, a file gone or changed) is downloaded **fresh** through the
  gateway's read-only download with its existing caps (``fresh_download.download_checked``,
  counted in the daily track-download limit, checked for all misses together first), then copied
  to the volume and its row stored in a fenced transaction; a row it replaces is deleted with it
  and its file removed after the commit.

Every statement filters on ``owner_id`` as well as through row security. Nothing is logged but
counts. Output renders never read this cache (ADR 0036 section 6, unchanged).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import bindparam, text

from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.design import tracklimit
from classbeat.jobs import JobFailure, JobRun
from classbeat.preview import storage
from classbeat.swedebeat_studio.fresh_download import download_checked

_ROWS = text(
    "SELECT id, reference, sha256 FROM preview_sources "
    "WHERE owner_id = :owner_id AND class_id = :class_id"
)
_TOUCH = text(
    "UPDATE preview_sources SET last_used_at = now() WHERE owner_id = :owner_id AND id IN :ids"
).bindparams(bindparam("ids", expanding=True))
_DROP = text(
    "DELETE FROM preview_sources WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND reference = :reference RETURNING id"
)
_ADD = text(
    "INSERT INTO preview_sources (id, owner_id, class_id, reference, sha256, bytes) "
    "VALUES (:id, :owner_id, :class_id, :reference, :sha256, :bytes)"
)


@dataclass(frozen=True, slots=True)
class Cached:
    id: UUID
    sha256: str


@dataclass
class Gathered:
    """The tracks in the render's directory, by reference; files to remove after the run."""

    paths: dict[str, Path]
    replaced: list[Path]


async def _cached(run: JobRun, class_id: UUID) -> dict[str, Cached]:
    async with run.fenced() as fs:
        params = {"owner_id": fs.lease.owner_id, "class_id": class_id}
        rows = (await fs.db.execute(_ROWS, params)).all()
    return {str(r.reference): Cached(r.id, bytes(r.sha256).hex()) for r in rows}


async def _store(
    run: JobRun, root: Path, class_id: UUID, reference: str, downloaded: Path, sha256: str
) -> list[Path]:
    """Copy a fresh download to the volume and store its row; the files of replaced rows."""
    owner_id = run.lease.owner_id
    new_id = uuid.uuid4()
    placed = storage.place(downloaded, root, owner_id, "sources", new_id)
    if placed is None:
        return []  # the volume refused: the render still has its download, nothing is cached
    ids = {"owner_id": owner_id, "class_id": class_id, "reference": reference}
    try:
        async with run.fenced() as fs:
            gone = [r[0] for r in (await fs.db.execute(_DROP, ids)).all()]
            size = placed.stat().st_size
            await fs.db.execute(
                _ADD, {**ids, "id": new_id, "sha256": bytes.fromhex(sha256), "bytes": size}
            )
    except BaseException:
        storage.remove(placed)
        raise
    return [storage.path_of(root, owner_id, "sources", g) for g in gone]


async def gather(
    run: JobRun,
    bearer: SwedebeatBearer,
    key: SecretStr,
    root: Path,
    class_id: UUID,
    sources: Mapping[str, str],
    work: Path,
) -> Gathered:
    """Each distinct track of ``sources`` (reference -> analysed SHA-256) in ``work``."""
    owner_id = run.lease.owner_id
    cached = await _cached(run, class_id)
    done = Gathered({}, [])
    hits: list[UUID] = []
    misses: list[str] = []
    for n, (reference, analysed) in enumerate(sources.items()):
        dest = work / f"track-{n:02d}.mp3"
        found = cached.get(reference)
        if found is not None and found.sha256 == analysed:
            stored = storage.path_of(root, owner_id, "sources", found.id)
            if storage.copy_checked(stored, dest, analysed) is not None:
                done.paths[reference] = dest
                hits.append(found.id)
                continue
        misses.append(reference)
    async with run.fenced() as fs:
        if hits:
            await fs.db.execute(_TOUCH, {"owner_id": owner_id, "ids": hits})
        if misses:
            await tracklimit.check(fs, len(misses))
    for reference in misses:
        n = list(sources).index(reference)
        dest = work / f"track-{n:02d}.mp3"
        await download_checked(run, bearer, key, reference, dest, sources[reference])
        done.paths[reference] = dest
        done.replaced += await _store(run, root, class_id, reference, dest, sources[reference])
    if set(done.paths) != set(sources):  # pragma: no cover - every reference is a hit or a miss
        raise JobFailure("internal")
    return done
