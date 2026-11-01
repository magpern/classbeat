"""The music step for a Swedebeat mix (ADR 0016 section 3 steps 1 and 2; A3): the user's MOWL
playlist for the mix, then its analysis, from the user's own cache or from the audio.

``load_swedebeat_mix`` is what ``design_draft`` calls for the music source ``mowl_swedebeat_mix``
(wired by A4, behind ``swedebeat_enabled``), after ``design.music``'s pattern:

1. **The connection.** ``connection_id`` is the job's ``mowl_connection_id``; it must be the
   context's MOWL connection (``not_connected`` before any call). It is opened (a database read
   and a decryption, no request).
2. **The cache.** One fenced transaction share-locks the connection (``hold``, the connection
   guard of ``design.music``) and reads the user's ``swedebeat_mix_maps`` row for the mix and their
   stored link. It is a hit only if the row is younger than 30 days, was made for **this MOWL
   connection** (``mowl_connection_id``: the table has no foreign key to it, so this is compared
   here), names the link's MOWL playlist and has the library's current map version. **A hit makes
   no request at all.**
3. **A miss.** The user's fresh listing must hold the mix (``invalid_input`` otherwise) and gives
   its listed length; the attach (``operations.swedebeat``, which stores the link) and the wait for
   MOWL to index it; the temporary link from MOWL (``GET v1/swedebeat/playlist/{id}/
   TemporaryLinks``, one read through the library's call); the fetch (``mixfetch``: one
   no-redirect media client, the media list, 250 MB, raw bytes only) into a private directory; the
   analysis in a limited child process (``mixanalysis``); the directory is removed on every way
   out. The link lives in this function's local variable only.
4. **Storing.** One fenced transaction (the connection and the link held again) upserts the row,
   drops what is past 30 days and all but the newest 20 of the user.

Errors are ``JobFailure`` categories: ``media_host_not_allowed`` (an empty production list ends
every uncached mix so, with no media request), ``media_refused``, ``analysis_failed`` (undecodable
or too long audio, the child's memory or time limit, a crash), the connection's, and
``upstream_unavailable`` (a refused or empty link answer from MOWL, a slow read; retried).
Nothing in a message, a log or a row names a link, a signature or a file's text. Only counts are
logged.
"""

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, cast
from uuid import UUID

import httpx
from mowl import api as mowl_api
from mowl import music
from sqlalchemy import text

from classbeat.connections import OpenConnection, call_opened, hold, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.design.mixanalysis import (
    ANALYSIS_SECONDS,
    analyse_audio,
    decode_limit_seconds,
)
from classbeat.design.mixfetch import fetch_mix_audio, private_directory
from classbeat.egress import mowl_media_async_client
from classbeat.jobs import FencedSession, JobFailure, JobRun
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.operations.swedebeat import (
    attach_swedebeat_mix,
    check_mix_id,
    parse_mixes,
    wait_indexed,
)

logger = logging.getLogger(__name__)

MAPS_PER_USER: Final = 20
MAP_TTL_DAYS: Final = 30
LINKS_SECONDS: Final = 60.0  # the temporary-link read
FETCH_SECONDS: Final = 300.0  # the media client's total deadline
MAX_DURATION_SECONDS: Final = 7200.0

_CACHED = text(
    "SELECT m.mowl_connection_id, m.playlist_id, m.name, m.duration, m.listed_duration, m.map, "
    "l.playlist_id AS linked "
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
    "duration, listed_duration, map) "
    "VALUES (:owner_id, :mix_id, :connection_id, :playlist_id, :name, :duration, :listed, "
    "CAST(:map AS jsonb)) "
    "ON CONFLICT (owner_id, mix_id) DO UPDATE SET "
    "mowl_connection_id = EXCLUDED.mowl_connection_id, playlist_id = EXCLUDED.playlist_id, "
    "name = EXCLUDED.name, duration = EXCLUDED.duration, "
    "listed_duration = EXCLUDED.listed_duration, map = EXCLUDED.map, created_at = now()"
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
    of it (a later revise notices a re-analysis); ``cached`` is whether no audio was fetched."""

    mix_id: str
    playlist_id: int
    name: str
    duration: float
    listed_duration: float
    music_map: dict[str, Any]
    fingerprint: str
    cached: bool


def fingerprint(music_map: dict[str, Any]) -> str:
    """SHA-256 of the map's canonical JSON."""
    canonical = json.dumps(music_map, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _cached(
    run: JobRun, opened: OpenConnection, mix_id: str, connection_id: UUID
) -> MixAnalysis | None:
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, opened):
            raise JobFailure("not_connected")
        params = {"owner_id": run.lease.owner_id, "mix_id": mix_id, "days": MAP_TTL_DAYS}
        row = (await fs.db.execute(_CACHED, params)).first()
    if (
        row is None
        or row.mowl_connection_id != connection_id
        or row.linked is None
        or row.linked != row.playlist_id
    ):
        return None
    stored: object = row.map
    if not isinstance(stored, dict) or stored.get("version") != music.MAP_VERSION:  # pyright: ignore[reportUnknownMemberType]
        return None  # an older analysis format: redone, as the library does
    music_map = cast(dict[str, Any], stored)
    return MixAnalysis(
        mix_id,
        int(row.playlist_id),
        str(row.name),
        float(row.duration),
        float(row.listed_duration),
        music_map,
        fingerprint(music_map),
        cached=True,
    )


async def _extend(run: JobRun, seconds: float) -> None:
    async with run.fenced() as fs:
        await fs.extend_lease(seconds + CALL_LEASE_MARGIN_SECONDS)


def _temporary_link(answer: object) -> str:
    """The first temporary link of MOWL's answer; a missing, empty or malformed list is MOWL not
    being ready: the retryable ``upstream_unavailable``. Never logged."""
    links: object = answer.get("TemporaryLinks") if isinstance(answer, dict) else None  # pyright: ignore[reportUnknownMemberType]
    first: object = links[0] if isinstance(links, list) and links else None  # pyright: ignore[reportUnknownVariableType]
    link: object = first.get("TemporaryLink") if isinstance(first, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(link, str) or not link:
        raise JobFailure("upstream_unavailable")
    return link


async def _store(
    fs_run: JobRun, opened: OpenConnection, analysis: MixAnalysis, connection_id: UUID
) -> None:
    payload = json.dumps(analysis.music_map, separators=(",", ":"), allow_nan=False)
    async with fs_run.fenced() as fs:
        await _store_row(fs, fs_run, opened, analysis, connection_id, payload)


async def _store_row(
    fs: FencedSession,
    run: JobRun,
    opened: OpenConnection,
    analysis: MixAnalysis,
    connection_id: UUID,
    payload: str,
) -> None:
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
        },
    )
    await fs.db.execute(_EXPIRE, {"owner_id": owner, "days": MAP_TTL_DAYS})
    await fs.db.execute(_TRIM, {"owner_id": owner, "keep": MAPS_PER_USER})


async def load_swedebeat_mix(
    run: JobRun,
    keyring: KeyRing,
    mix_id: str,
    *,
    connection_id: UUID,
    client_factory: Callable[[], httpx.AsyncClient] = mowl_media_async_client,
    clock: Callable[[], float] = time.monotonic,
    index_poll_seconds: float | None = None,
) -> MixAnalysis:
    """The user's analysis of their Swedebeat mix ``mix_id`` (module docstring). ``client_factory``
    builds the one media client of a fetch; production passes none (the guard refuses a client
    built anywhere but ``classbeat.egress``)."""
    mix_id = check_mix_id(mix_id)
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    hit = await _cached(run, opened, mix_id, connection_id)
    if hit is not None:
        logger.info(
            "swedebeat mix from cache",
            extra={"job_id": str(run.lease.job_id), "songs": len(hit.music_map["songs"])},
        )
        return hit
    await run.checkpoint()
    listing = await call_opened(
        run.factory, run.ctx, opened, lambda creds: mowl_api.swedebeat_mixes(creds)
    )
    entry = next((m for m in parse_mixes(listing) if m.mix_id == mix_id), None)
    if entry is None:
        raise JobFailure("invalid_input")  # not in the user's own listing: nothing attached
    playlist_id = await attach_swedebeat_mix(run, keyring, mix_id, connection_id=connection_id)
    poll = {} if index_poll_seconds is None else {"poll_seconds": index_poll_seconds}
    indexed = await wait_indexed(run, keyring, playlist_id, connection_id=connection_id, **poll)
    listed = float(entry.duration_seconds) if entry.duration_seconds is not None else None
    duration = indexed.duration_seconds
    if not 0 < duration <= MAX_DURATION_SECONDS:
        raise JobFailure("analysis_failed")
    limit = decode_limit_seconds(listed if listed is not None else duration)
    await _extend(run, LINKS_SECONDS)
    await run.checkpoint()
    answer = await call_opened(
        run.factory,
        run.ctx,
        opened,
        lambda creds: mowl_api.call(
            creds, "GET", f"v1/swedebeat/playlist/{playlist_id}/TemporaryLinks"
        ),
    )
    link = _temporary_link(answer)
    del answer
    await _extend(run, FETCH_SECONDS + ANALYSIS_SECONDS)
    with private_directory() as directory:
        audio = await fetch_mix_audio(run, link, directory, client_factory=client_factory)
        del link
        result = await analyse_audio(
            run, audio, max_seconds=limit, map_version=music.MAP_VERSION, clock=clock
        )
    analysis = MixAnalysis(
        mix_id,
        playlist_id,
        entry.name,
        round(duration, 3),
        float(listed if listed is not None else 0.0),
        result.music_map,
        fingerprint(result.music_map),
        cached=False,
    )
    await _store(run, opened, analysis, connection_id)
    logger.info(
        "swedebeat mix analysed",
        extra={
            "job_id": str(run.lease.job_id),
            "songs": len(analysis.music_map["songs"]),
            "peak_rss_mb": None if result.peak_rss is None else result.peak_rss // (1 << 20),
        },
    )
    return analysis
