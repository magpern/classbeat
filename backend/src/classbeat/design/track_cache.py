"""The user's own cache of studio track analyses (ADR 0036 section 4.1, SR C4):
``swedebeat_track_maps``, one row per ``(owner_id, reference, analyser_version)``.

- **A hit** (``cached``) is a row of the caller's references younger than ``MAP_TTL_DAYS``, of the
  library's current ``TRACK_VERSION``, whose map still passes ``valid_track_map``. It is read in a
  fenced transaction that share-locks the job's SwedeBeat connection (``hold``): a run whose
  connection is gone (a Disconnect forgot the rows) or was reconnected meanwhile ends
  ``not_connected`` before any download. **A hit needs no download.**
- **A store** (``store``) is one fenced transaction per analysed track: the connection held again
  (so a Disconnect that forgot the rows cannot be undone by a late store), the row upserted with
  the SHA-256 of the analysed bytes, then the user's rows past ``MAP_TTL_DAYS`` and all but the
  newest ``MAPS_PER_USER`` dropped, as ``swedebeat_mix_maps`` does. The last store of a run frees
  its mix-analysis slot (#574).

Every statement filters on ``owner_id`` itself as well as through row security. Nothing here logs.
"""

import json
from dataclasses import dataclass
from typing import Any, Final, cast

from mowl import track
from sqlalchemy import bindparam, text

from classbeat.connections import OpenConnection, hold
from classbeat.design.track_map_policy import valid_track_map
from classbeat.jobs import JobFailure, JobRun

MAP_TTL_DAYS: Final = 30
MAPS_PER_USER: Final = 200  # five classes of 40 tracks

_CACHED = text(
    "SELECT reference, sha256, map FROM swedebeat_track_maps "
    "WHERE owner_id = :owner_id AND analyser_version = :version AND reference IN :references "
    "AND created_at > now() - make_interval(days => :days)"
).bindparams(bindparam("references", expanding=True))
_UPSERT = text(
    "INSERT INTO swedebeat_track_maps (owner_id, reference, analyser_version, sha256, map) "
    "VALUES (:owner_id, :reference, :version, :sha256, CAST(:map AS jsonb)) "
    "ON CONFLICT (owner_id, reference, analyser_version) DO UPDATE SET "
    "sha256 = EXCLUDED.sha256, map = EXCLUDED.map, created_at = now()"
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
    analysed bytes, and whether it came from the cache (no download)."""

    reference: str
    sha256: str
    music_map: dict[str, Any]
    cached: bool


async def cached(
    run: JobRun, connection: OpenConnection, references: list[str]
) -> dict[str, TrackMap]:
    """The user's current analyses of ``references`` (the hits only)."""
    params = {
        "owner_id": run.lease.owner_id,
        "version": track.TRACK_VERSION,
        "references": references,
        "days": MAP_TTL_DAYS,
    }
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, connection):
            raise JobFailure("not_connected")
        rows = (await fs.db.execute(_CACHED, params)).all() if references else []
    hits: dict[str, TrackMap] = {}
    for row in rows:
        stored: object = row.map
        if not valid_track_map(stored, track.TRACK_VERSION):
            continue  # not what this version reads: analysed again
        music_map = cast(dict[str, Any], stored)
        hits[str(row.reference)] = TrackMap(
            str(row.reference), bytes(row.sha256).hex(), music_map, cached=True
        )
    return hits


async def store(
    run: JobRun, connection: OpenConnection, analysed: TrackMap, *, release_slot: bool
) -> None:
    """Keep ``analysed`` for the user (``not_connected`` when the connection is gone)."""
    owner = run.lease.owner_id
    payload = json.dumps(analysed.music_map, separators=(",", ":"), allow_nan=False)
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
            },
        )
        await fs.db.execute(_EXPIRE, {"owner_id": owner, "days": MAP_TTL_DAYS})
        await fs.db.execute(_TRIM, {"owner_id": owner, "keep": MAPS_PER_USER})
        if release_slot:
            await run.release_analysis_slot(fs)  # #574: free once this commits
    if release_slot:
        run.slot_released()
