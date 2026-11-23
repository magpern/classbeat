"""The user's cache of their mix analyses, ``swedebeat_mix_maps`` (ADR 0016 section 3 steps 2 and
4; ADR 0040 sections 5 and 6; split out of ``design.swedebeat_music``).

**One row per mix** (key ``(owner_id, mix_id)``): spotdown is the only analysis path (ADR 0040
amendment), so a row is spotdown's map (``spotdown.mix_map_model``, with its ``provenance``);
migration 0077 deleted the ``local`` rows (#1357) and 0081 dropped the ``analyser`` column, which
then admitted only ``spotdown``, and made ``provenance`` required (#1299).

- ``cached``: one fenced transaction share-locks the connection (``hold``) and reads the user's
  rows for the mix with their stored link. A row counts only if it is younger than
  ``MAP_TTL_DAYS``, was made for **this MOWL connection**, names the link's MOWL playlist and has
  the library's current map version. Of those, the one whose map has
  the fingerprint ``prefer`` (a revise or refit: the analysis its base was designed on) comes
  first; else one whose provenance is this build's (``mix_map_model.current_mix_provenance``). A
  base whose row is gone finds no row with its fingerprint, so its revise is told
  ``music_changed`` by the fingerprint check. **A hit makes no request at all.**
- ``store``: a map without its provenance is ``internal`` before anything is written (the table's
  ``NOT NULL``, as ``track_cache.store``); then one fenced transaction (the connection and the
  link held again) upserts the row,
  drops what is past 30 days and all but the newest ``MAPS_PER_USER`` rows of the user (one row
  per mix), frees the job's spotdown slot, marks the job as one that ran a fresh analysis
  (``jobs.mail_handoff``) and records the fetch and analysis times (``jobs.analysis_times``;
  ``jobs.analysis_decode_ms`` is spotdown's analysis time, since #1353).

Every statement filters on ``owner_id`` itself as well as through row security. Nothing here logs.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import UUID

from mowl import music
from sqlalchemy import text

from classbeat.connections import OpenConnection, hold
from classbeat.jobs import FencedSession, JobFailure, JobRun, analysis_times, mail_handoff
from classbeat.spotdown.mix_map_model import current_mix_provenance

MAPS_PER_USER: Final = 20
MAP_TTL_DAYS: Final = 30

_CACHED = text(
    "SELECT m.mowl_connection_id, m.playlist_id, m.name, m.duration, m.listed_duration, m.map, "
    "m.provenance, l.playlist_id AS linked "
    "FROM swedebeat_mix_maps m "
    "LEFT JOIN mowl_playlist_links l ON l.owner_id = m.owner_id AND l.source = 'swedebeat' "
    "AND l.source_id = m.mix_id "
    "WHERE m.owner_id = :owner_id AND m.mix_id = :mix_id "
    "AND m.created_at > now() - make_interval(days => :days)"
)
_LINK = text(
    "SELECT playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND source = 'swedebeat' AND source_id = :mix_id"
)
_UPSERT = text(
    "INSERT INTO swedebeat_mix_maps (owner_id, mix_id, mowl_connection_id, playlist_id, name, "
    "duration, listed_duration, map, provenance) "
    "VALUES (:owner_id, :mix_id, :connection_id, :playlist_id, :name, :duration, :listed, "
    "CAST(:map AS jsonb), CAST(:provenance AS jsonb)) "
    "ON CONFLICT (owner_id, mix_id) DO UPDATE SET "
    "mowl_connection_id = EXCLUDED.mowl_connection_id, playlist_id = EXCLUDED.playlist_id, "
    "name = EXCLUDED.name, duration = EXCLUDED.duration, "
    "listed_duration = EXCLUDED.listed_duration, map = EXCLUDED.map, "
    "provenance = EXCLUDED.provenance, created_at = now()"
)
_EXPIRE = text(
    "DELETE FROM swedebeat_mix_maps "
    "WHERE owner_id = :owner_id AND created_at <= now() - make_interval(days => :days)"
)
_TRIM = text(
    "DELETE FROM swedebeat_mix_maps WHERE owner_id = :owner_id AND id IN ("
    "SELECT id FROM swedebeat_mix_maps WHERE owner_id = :owner_id "
    "ORDER BY created_at DESC, id OFFSET :keep)"
)


@dataclass(frozen=True, slots=True)
class MixAnalysis:
    """The user's analysis of one mix. ``music_map`` is the library's map; ``fingerprint`` a hash
    of it (a later revise notices a re-analysis); ``cached`` is whether no audio was fetched;
    and ``provenance`` is spotdown's record."""

    mix_id: str
    playlist_id: int
    name: str
    duration: float
    listed_duration: float
    music_map: dict[str, Any]
    fingerprint: str
    cached: bool
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Timing:
    """Seconds a fresh analysis spent fetching the audio and analysing it (ADR 0026 stage 1,
    #735)."""

    fetch: float
    decode: float


def fingerprint(music_map: dict[str, Any]) -> str:
    """SHA-256 of the map's canonical JSON."""
    canonical = json.dumps(music_map, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _analysis(row: Any, mix_id: str, connection_id: UUID) -> MixAnalysis | None:
    if row.mowl_connection_id != connection_id or row.linked is None:
        return None
    if row.linked != row.playlist_id:
        return None
    stored: object = row.map
    if not isinstance(stored, dict) or stored.get("version") != music.MAP_VERSION:  # pyright: ignore[reportUnknownMemberType]
        return None  # an older analysis format: redone, as the library does
    music_map = cast(dict[str, Any], stored)
    provenance = cast(dict[str, Any] | None, row.provenance)
    return MixAnalysis(
        mix_id,
        int(row.playlist_id),
        str(row.name),
        float(row.duration),
        float(row.listed_duration),
        music_map,
        fingerprint(music_map),
        cached=True,
        provenance=provenance,
    )


def choose(found: list[MixAnalysis], *, prefer: str | None) -> MixAnalysis | None:
    """The hit among the usable rows (module docstring)."""
    if prefer is not None:
        same = next((a for a in found if a.fingerprint == prefer), None)
        if same is not None:
            return same
    return next((a for a in found if current_mix_provenance(a.provenance)), None)


async def cached(
    run: JobRun,
    opened: OpenConnection,
    mix_id: str,
    connection_id: UUID,
    *,
    prefer: str | None = None,
) -> MixAnalysis | None:
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, opened):
            raise JobFailure("not_connected")
        params = {"owner_id": run.lease.owner_id, "mix_id": mix_id, "days": MAP_TTL_DAYS}
        rows = (await fs.db.execute(_CACHED, params)).all()
    found = [a for a in (_analysis(r, mix_id, connection_id) for r in rows) if a is not None]
    return choose(found, prefer=prefer)


async def store(
    run: JobRun,
    opened: OpenConnection,
    analysis: MixAnalysis,
    connection_id: UUID,
    timing: Timing,
) -> None:
    if analysis.provenance is None:
        raise JobFailure("internal")  # the table's NOT NULL (migration 0081), before any write
    record = json.dumps(analysis.provenance, separators=(",", ":"), allow_nan=False)
    async with run.fenced() as fs:
        await _store_row(fs, run, opened, analysis, connection_id, record)
        await run.release_spotdown_slot(fs)  # #574: the slot is free once this commits
        await mail_handoff.note_fresh_analysis(fs)  # ME5: the job ran a fresh analysis
        # ADR 0026 stage 1 (#735): for the p95 of ``queue_stats()``.
        await analysis_times.note_analysis_times(fs, timing.fetch, timing.decode)
    run.slot_released()


async def _store_row(
    fs: FencedSession,
    run: JobRun,
    opened: OpenConnection,
    analysis: MixAnalysis,
    connection_id: UUID,
    record: str,
) -> None:
    payload = json.dumps(analysis.music_map, separators=(",", ":"), allow_nan=False)
    owner = run.lease.owner_id
    if not await hold(fs.db, run.ctx, opened):
        raise JobFailure("not_connected")
    link = (await fs.db.execute(_LINK, {"owner_id": owner, "mix_id": analysis.mix_id})).first()
    if link is None or link.playlist_id != analysis.playlist_id:  # a reconnect deleted it
        raise JobFailure("not_connected")
    await fs.db.execute(
        _UPSERT,
        {
            "owner_id": owner,
            "mix_id": analysis.mix_id,
            "connection_id": connection_id,
            "playlist_id": analysis.playlist_id,
            "name": analysis.name,
            "duration": analysis.duration,
            "listed": analysis.listed_duration,
            "map": payload,
            "provenance": record,
        },
    )
    await fs.db.execute(_EXPIRE, {"owner_id": owner, "days": MAP_TTL_DAYS})
    await fs.db.execute(_TRIM, {"owner_id": owner, "keep": MAPS_PER_USER})
