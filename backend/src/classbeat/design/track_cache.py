"""The user's own cache of studio track analyses (ADR 0036 section 4.1, SR C4):
``swedebeat_track_maps``, one row per ``(owner_id, reference, analyser_version, analyser)``.

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
  its mix-analysis slot (#574).
- **The analyser** (ADR 0040 section 6, #1302): a row is ``local`` (``mowl.music.analyze_track``)
  or ``spotdown`` (spotdown's analysis of the same bytes, with its ``provenance`` record). A
  track's two rows coexist; a store replaces only the row of its own analyser, so a class
  designed on one analyser's map is never moved to the other's. Every read takes an ``Accept``
  rule over the ``Stored`` row: ``track_analysis`` takes ``local`` rows and, while spotdown is
  the owner's source, current ``spotdown`` ones (a ``spotdown`` hit before a ``local`` one);
  revise, outputs and the timeline take exactly the row the draft recorded (``recorded``).

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
from classbeat.design.studio_revision_model import Analyser, Wanted, map_hash
from classbeat.design.track_map_policy import valid_track_map
from classbeat.jobs import JobFailure, JobRun

MAP_TTL_DAYS: Final = 30
MAPS_PER_USER: Final = 200  # five classes of 40 tracks

ANALYSERS: Final[frozenset[str]] = frozenset({"local", "spotdown"})


@dataclass(frozen=True, slots=True)
class Stored:
    """A row as an ``Accept`` rule sees it."""

    reference: str
    analyser: str
    sha256: str
    provenance: object
    music_map: Mapping[str, Any]


Accept = Callable[[Stored], bool]

_COLUMNS = "reference, sha256, map, analyser, provenance FROM swedebeat_track_maps "
_FRESH = "AND created_at > now() - make_interval(days => :days)"
_CACHED = text(
    f"SELECT {_COLUMNS}WHERE owner_id = :owner_id AND analyser_version = :version "
    f"AND reference IN :references {_FRESH}"
).bindparams(bindparam("references", expanding=True))
_BY_SHA256 = text(
    f"SELECT {_COLUMNS}WHERE owner_id = :owner_id AND analyser_version = :version "
    f"AND sha256 = :sha256 {_FRESH} ORDER BY (analyser = 'spotdown') DESC, created_at DESC, id"
)
_UPSERT = text(
    "INSERT INTO swedebeat_track_maps "
    "(owner_id, reference, analyser_version, sha256, map, analyser, provenance) "
    "VALUES (:owner_id, :reference, :version, :sha256, CAST(:map AS jsonb), :analyser, "
    "CAST(:provenance AS jsonb)) "
    "ON CONFLICT (owner_id, reference, analyser_version, analyser) DO UPDATE SET "
    "sha256 = EXCLUDED.sha256, map = EXCLUDED.map, provenance = EXCLUDED.provenance, "
    "created_at = now()"
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
    analysed bytes, whether it came from the cache (no download), what analysed it and, for
    spotdown, its provenance record."""

    reference: str
    sha256: str
    music_map: dict[str, Any]
    cached: bool
    analyser: Analyser = "local"
    provenance: Mapping[str, object] | None = None


def _hit(row: Any, accept: Accept, reference: str | None = None) -> TrackMap | None:
    stored: object = row.map
    analyser, provenance, sha256 = str(row.analyser), row.provenance, bytes(row.sha256).hex()
    if analyser not in ANALYSERS or not valid_track_map(stored, track.TRACK_VERSION):
        return None  # not what this version reads: analysed again
    music_map = cast(dict[str, Any], stored)
    if not accept(Stored(str(row.reference), analyser, sha256, provenance, music_map)):
        return None
    return TrackMap(
        reference or str(row.reference),
        sha256,
        music_map,
        cached=True,
        analyser=cast(Analyser, analyser),
        provenance=cast(Mapping[str, object], provenance) if provenance is not None else None,
    )


async def cached_in(
    db: AsyncSession, owner_id: UUID, references: list[str], accept: Accept
) -> dict[str, TrackMap]:
    """The user's current analyses of ``references`` (the hits only, those ``accept`` takes; a
    ``spotdown`` one before a ``local`` one), in the caller's transaction: a page's read (the
    timeline), which holds no connection and writes nothing."""
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
        if hit is not None and (hit.reference not in hits or hit.analyser == "spotdown"):
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
    """The user's current analysis of the same bytes under any reference (a ``spotdown`` one
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
) -> None:
    """Keep ``analysed`` for the user (``not_connected`` when the connection is gone)."""
    owner = run.lease.owner_id
    payload = json.dumps(analysed.music_map, separators=(",", ":"), allow_nan=False)
    provenance = analysed.provenance
    if (analysed.analyser == "spotdown") != (provenance is not None):
        raise JobFailure("internal")  # the table's check, before anything is written
    record = None if provenance is None else json.dumps(dict(provenance), allow_nan=False)
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, connection):
            raise JobFailure("not_connected")
        await fs.db.execute(
            _UPSERT,
            {
                "owner_id": owner,
                "reference": analysed.reference,
                "version": track.TRACK_VERSION,
                "sha256": bytes.fromhex(analysed.sha256),
                "map": payload,
                "analyser": analysed.analyser,
                "provenance": record,
            },
        )
        await fs.db.execute(_EXPIRE, {"owner_id": owner, "days": MAP_TTL_DAYS})
        await fs.db.execute(_TRIM, {"owner_id": owner, "keep": MAPS_PER_USER})
        if release_slot:
            await run.release_analysis_slot(fs)  # #574: free once this commits
    if release_slot:
        run.slot_released()


def recorded(wanted: Mapping[str, Wanted]) -> Accept:
    """Exactly the rows a draft recorded: each reference's analyser and, when recorded, bytes."""

    def accepted(row: Stored) -> bool:
        want = wanted.get(row.reference)
        if want is None or row.analyser != want.analyser:
            return False
        if want.sha256 is None:
            return True  # a draft stored before #1302: its local row, as before
        return want.sha256 == row.sha256 and want.map_hash == map_hash(row.music_map)

    return accepted
