"""The user's own cache of studio track analyses (ADR 0036 section 4.1, SR C4):
``swedebeat_track_maps``, one row per ``(owner_id, reference, analyser_version)``.

- **A hit** (``cached``) is a row of the caller's references younger than ``MAP_TTL_DAYS``, of the
  library's current ``TRACK_VERSION``, whose map still passes ``valid_track_map``. It is read in a
  fenced transaction that share-locks the job's SwedeBeat connection (``hold``): a run whose
  connection is gone (a Disconnect forgot the rows) or was reconnected meanwhile ends
  ``not_connected`` before any download. **A hit needs no download.** ``cached_in`` is the same
  read in the caller's transaction, for a page (the studio timeline), with no connection held.
- **A store** (``store``) is one fenced transaction per analysed track: the connection held again
  (so a Disconnect that forgot the rows cannot be undone by a late store), the row upserted with
  the SHA-256 of the analysed bytes, then the user's rows past ``MAP_TTL_DAYS`` and all but the
  newest ``MAPS_PER_USER`` dropped, as ``swedebeat_mix_maps`` does. The last store of a run frees
  its spotdown slot (#574). The run goes on with the map **as stored**
  (jsonb's form, ``RETURNING map``), the one a later read sees and hashes.
- **spotdown's rows** (ADR 0040 section 6, #1302; amendment "spotdown is the only analysis path",
  #1356, #1299): every row is spotdown's analysis of the same bytes, with its ``provenance`` record
  (migration 0075 deleted the ``local`` rows; 0081 dropped the ``analyser`` column, which then
  admitted only ``spotdown``, and made ``provenance`` required). Every read takes an ``Accept``
  rule over the ``Stored`` row: ``track_analysis`` takes only rows whose provenance is this
  build's; revise, outputs and the timeline take exactly the row the draft recorded
  (``recorded``).

Every statement filters on ``owner_id`` itself as well as through row security. Nothing here logs.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import UUID

from mowl import track
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import OpenConnection, hold
from classbeat.design.studio_revision_model import Wanted, map_hash
from classbeat.design.track_map_policy import valid_track_map
from classbeat.jobs import JobFailure, JobRun

MAP_TTL_DAYS: Final = 30
MAPS_PER_USER: Final = 200  # five classes of 40 tracks


@dataclass(frozen=True, slots=True)
class Stored:
    """A row as an ``Accept`` rule sees it."""

    reference: str
    sha256: str
    provenance: object
    music_map: Mapping[str, Any]


Accept = Callable[[Stored], bool]

_COLUMNS = "reference, sha256, map, provenance FROM swedebeat_track_maps "
_FRESH = "AND created_at > now() - make_interval(days => :days)"
_CACHED = text(
    f"SELECT {_COLUMNS}WHERE owner_id = :owner_id AND analyser_version = :version "
    f"AND reference IN :references {_FRESH}"
).bindparams(bindparam("references", expanding=True))
_BY_SHA256 = text(
    f"SELECT {_COLUMNS}WHERE owner_id = :owner_id AND analyser_version = :version "
    f"AND sha256 = :sha256 {_FRESH} ORDER BY created_at DESC, id"
)
_UPSERT = text(
    "INSERT INTO swedebeat_track_maps "
    "(owner_id, reference, analyser_version, sha256, map, provenance) "
    "VALUES (:owner_id, :reference, :version, :sha256, CAST(:map AS jsonb), "
    "CAST(:provenance AS jsonb)) "
    "ON CONFLICT (owner_id, reference, analyser_version) DO UPDATE SET "
    "sha256 = EXCLUDED.sha256, map = EXCLUDED.map, provenance = EXCLUDED.provenance, "
    "created_at = now() RETURNING map"
)
_EXPIRE = text(
    "DELETE FROM swedebeat_track_maps "
    "WHERE owner_id = :owner_id AND created_at <= now() - make_interval(days => :days)"
)
_TRIM = text(
    "DELETE FROM swedebeat_track_maps WHERE owner_id = :owner_id AND id IN ("
    "SELECT id FROM swedebeat_track_maps WHERE owner_id = :owner_id "
    "ORDER BY created_at DESC, id OFFSET :keep)"
)


@dataclass(frozen=True, slots=True)
class TrackMap:
    """The user's analysis of one catalogue track: the library's map, the hex SHA-256 of the
    analysed bytes, whether it came from the cache (no download) and spotdown's provenance
    record."""

    reference: str
    sha256: str
    music_map: dict[str, Any]
    cached: bool
    provenance: Mapping[str, object] | None = None


def _hit(row: Any, accept: Accept, reference: str | None = None) -> TrackMap | None:
    stored: object = row.map
    provenance, sha256 = row.provenance, bytes(row.sha256).hex()
    if not valid_track_map(stored, track.TRACK_VERSION):
        return None  # not what this version reads: analysed again
    music_map = cast(dict[str, Any], stored)
    if not accept(Stored(str(row.reference), sha256, provenance, music_map)):
        return None
    return TrackMap(
        reference or str(row.reference),
        sha256,
        music_map,
        cached=True,
        provenance=cast(Mapping[str, object], provenance) if provenance is not None else None,
    )


async def cached_in(
    db: AsyncSession, owner_id: UUID, references: list[str], accept: Accept
) -> dict[str, TrackMap]:
    """The user's current analyses of ``references`` (the hits only, those ``accept`` takes), in the
    caller's transaction: a page's read (the timeline), which holds no connection and writes
    nothing."""
    params = {
        "owner_id": owner_id,
        "version": track.TRACK_VERSION,
        "references": references,
        "days": MAP_TTL_DAYS,
    }
    rows = (await db.execute(_CACHED, params)).all() if references else []
    hits: dict[str, TrackMap] = {}
    for row in rows:
        hit = _hit(row, accept)
        if hit is not None:
            hits[hit.reference] = hit
    return hits


async def cached(
    run: JobRun, connection: OpenConnection, references: list[str], accept: Accept
) -> dict[str, TrackMap]:
    """The user's current analyses of ``references`` (the hits only), with the connection held."""
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, connection):
            raise JobFailure("not_connected")
        return await cached_in(fs.db, run.lease.owner_id, references, accept)


async def cached_by_sha256(
    run: JobRun, connection: OpenConnection, reference: str, sha256: str, accept: Accept
) -> TrackMap | None:
    """The user's current analysis of the same bytes under any reference (the newest
    first), as ``reference``'s, with the connection held; ``None`` when there is none."""
    params = {
        "owner_id": run.lease.owner_id,
        "version": track.TRACK_VERSION,
        "sha256": bytes.fromhex(sha256),
        "days": MAP_TTL_DAYS,
    }
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, connection):
            raise JobFailure("not_connected")
        rows = (await fs.db.execute(_BY_SHA256, params)).all()
    for row in rows:
        if (hit := _hit(row, accept, reference)) is not None:
            return hit
    return None


async def store(
    run: JobRun, connection: OpenConnection, analysed: TrackMap, *, release_slot: bool
) -> TrackMap:
    """Keep ``analysed`` for the user (``not_connected`` when the connection is gone); answers it
    with the map **as stored**: jsonb's form, which a later read sees (``-0.0`` is ``0.0``,
    ``1e+16`` an integer), so a draft records the hash of what revise, outputs and the timeline
    read (``recorded``)."""
    owner = run.lease.owner_id
    payload = json.dumps(analysed.music_map, separators=(",", ":"), allow_nan=False)
    provenance = analysed.provenance
    if provenance is None:
        raise JobFailure("internal")  # the table's check, before anything is written
    record = json.dumps(dict(provenance), allow_nan=False)
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, connection):
            raise JobFailure("not_connected")
        kept = await fs.db.scalar(
            _UPSERT,
            {
                "owner_id": owner,
                "reference": analysed.reference,
                "version": track.TRACK_VERSION,
                "sha256": bytes.fromhex(analysed.sha256),
                "map": payload,
                "provenance": record,
            },
        )
        await fs.db.execute(_EXPIRE, {"owner_id": owner, "days": MAP_TTL_DAYS})
        await fs.db.execute(_TRIM, {"owner_id": owner, "keep": MAPS_PER_USER})
        if release_slot:
            await run.release_spotdown_slot(fs)  # #574: free once this commits
    if release_slot:
        run.slot_released()
    return TrackMap(  # the map as stored: a jsonb object reads back as a dict
        analysed.reference,
        analysed.sha256,
        cast(dict[str, Any], kept),
        cached=analysed.cached,
        provenance=analysed.provenance,
    )


def recorded(wanted: Mapping[str, Wanted]) -> Accept:
    """Exactly the rows a draft recorded: each reference's bytes and map."""

    def accepted(row: Stored) -> bool:
        want = wanted.get(row.reference)
        if want is None:
            return False
        return want.sha256 == row.sha256 and want.map_hash == map_hash(row.music_map)

    return accepted
