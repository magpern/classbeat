"""The music step for a Swedebeat mix (ADR 0016 section 3 steps 1 and 2; A3): the user's MOWL
playlist for the mix, then its analysis, from the user's own cache or from the audio.

``swedebeat_step`` (over ``load_swedebeat_mix``) is what ``design_draft`` and ``revise_draft`` call
for the music source ``mowl_swedebeat_mix`` (A4). It never reads ``swedebeat_enabled`` itself: the
kinds' ``admit`` and their run check the flag before calling it. After ``design.music``'s pattern:

1. **The connection.** ``connection_id`` is the job's ``mowl_connection_id``; it must be the
   context's MOWL connection (``not_connected`` before any call). It is opened (a database read
   and a decryption, no request).
2. **The cache** (``mix_cache.cached``). One fenced transaction share-locks the connection
   (``hold``, the connection guard of ``design.music``) and reads the user's ``swedebeat_mix_maps``
   rows for the mix and their stored link. A row counts only if it is a ``spotdown`` one with
   this build's provenance (the table holds no other, #1357), younger than 30 days, made for
   **this MOWL connection** (``mowl_connection_id``: the table has no foreign key to it, so this
   is compared there), names the link's MOWL playlist and has the library's current map version;
   a revise or refit (``prefer``) takes the row its base was designed on first. **A hit makes no
   request at all.**
3. **A miss.** With spotdown not usable in this worker the run ends ``analysis_unavailable``
   (``mix_spotdown.session_for``) before anything else. A run without one of the worker's
   mix-analysis slots (#574,
   ``JobRun.require_analysis_slot``) stops here and its job is requeued with one, the attempt
   not counted; ``queue_claim`` then claims it only while a slot is free
   (``CLASSBEAT_MIX_ANALYSIS_CONCURRENCY``). Then one of the user's daily analysis slots
   (``mixlimit.reserve``, ``analysis_limit`` past 10 in 24 hours; A4), then the user's fresh
   listing must hold the mix (``invalid_input`` otherwise) and gives
   its listed length; the attach (``operations.swedebeat``, which stores the link) and the wait for
   MOWL to index it; the temporary link from MOWL (``GET v1/swedebeat/playlist/{id}/
   TemporaryLinks``, one read through the library's call); the fetch (``mixfetch``: one
   no-redirect media client, the media list, 250 MB, raw bytes only) into a private directory; the
   analysis by **spotdown**, the only analysis path (``mix_spotdown``; ADR 0040 amendment, #1353):
   while the fetched file is on disk its bytes are relayed to spotdown (its lookup by sha256 in the
   owner's scope, then the upload) and an ``exact`` map is used with its provenance; any other
   outcome ends the job with a category (``analysis_unavailable``, retried, or
   ``analysis_failed``). The directory is removed on every way out. The link lives in this
   function's local variable only.
4. **Storing** (``mix_cache.store``, the ``spotdown`` row). One fenced
   transaction (the connection and the link held again) upserts the row,
   drops what is past 30 days and all but the newest 20 of the user, frees the job's analysis
   slot, marks the job as one that ran a fresh analysis (``jobs.fresh_analysis``, for the
   "class ready" mail of ME5: ``classbeat.jobs.mail_handoff``) and records how long the fetch and
   the decode took (``jobs.analysis_fetch_ms`` and ``analysis_decode_ms``, ADR 0026 stage 1:
   ``classbeat.jobs.analysis_times``). The log line of the analysis carries the same two numbers
   and the host's load average, never a link or a name.

Errors are ``JobFailure`` categories: ``media_host_not_allowed`` (an empty production list ends
every uncached mix so, with no media request), ``media_refused``, ``analysis_unavailable`` (retried)
and ``analysis_failed`` (``spotdown_ending``; also a mix of no or over 2 hours), the connection's,
and ``upstream_unavailable`` (a refused or empty link answer from MOWL, a slow read; retried).
Nothing in a message, a log or a row names a link, a signature or a file's text. Only counts are
logged.
"""

import logging
import os
import time
from collections.abc import Callable
from typing import Final
from uuid import UUID

import httpx
from mowl import api as mowl_api
from sqlalchemy import text

from classbeat.agent.mix_input import MixData, unsure_beat_one
from classbeat.connections import OpenConnection, call_opened, hold, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.design import mix_spotdown, mixlimit
from classbeat.design.mix_cache import MAP_TTL_DAYS as MAP_TTL_DAYS
from classbeat.design.mix_cache import MAPS_PER_USER as MAPS_PER_USER
from classbeat.design.mix_cache import MixAnalysis, Timing, cached, store
from classbeat.design.mix_cache import fingerprint as fingerprint
from classbeat.design.mix_facts import MixFacts
from classbeat.design.mixfetch import fetch_mix_audio, private_directory
from classbeat.design.music import MusicStep
from classbeat.egress import mowl_media_async_client
from classbeat.jobs import JobFailure, JobRun, analysis_times
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.operations.swedebeat import (
    SOURCE,
    attach_swedebeat_mix,
    check_mix_id,
    parse_mixes,
    wait_indexed,
)

logger = logging.getLogger(__name__)

LINKS_SECONDS: Final = 60.0  # the temporary-link read
FETCH_SECONDS: Final = 300.0  # the media client's total deadline
MAX_DURATION_SECONDS: Final = 7200.0


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


def _load_average() -> float | None:
    """The host's one-minute load average (a container sees the host's), or ``None`` where the
    platform has none: logged with each analysis so CPU pressure on the box can be read beside the
    analysis times (ADR 0026 stage 1)."""
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return None


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
    analysis, _opened = await open_swedebeat_mix(
        run,
        keyring,
        mix_id,
        connection_id=connection_id,
        client_factory=client_factory,
        clock=clock,
        index_poll_seconds=index_poll_seconds,
    )
    return analysis


async def open_swedebeat_mix(
    run: JobRun,
    keyring: KeyRing,
    mix_id: str,
    *,
    connection_id: UUID,
    client_factory: Callable[[], httpx.AsyncClient] = mowl_media_async_client,
    clock: Callable[[], float] = time.monotonic,
    index_poll_seconds: float | None = None,
    prefer: str | None = None,
) -> tuple[MixAnalysis, OpenConnection]:
    """``load_swedebeat_mix`` and the MOWL connection it was read with, for the connection guard
    of the transaction that stores what was designed on it (``swedebeat_step``). A cache miss
    first takes one of the user's daily analysis slots (``mixlimit.reserve``: ``analysis_limit``,
    before anything is listed, attached or fetched). ``prefer``: the fingerprint of the map a
    revise's or refit's base was designed on (``mix_cache.cached``)."""
    mix_id = check_mix_id(mix_id)
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    hit = await cached(run, opened, mix_id, connection_id, prefer=prefer)
    if hit is not None:
        if run.analysis_slot:  # requeued for a slot, then another job stored the analysis
            async with run.fenced() as fs:
                await run.release_analysis_slot(fs)
            run.slot_released()
        logger.info(
            "swedebeat mix from cache",
            extra={"job_id": str(run.lease.job_id), "songs": len(hit.music_map["songs"])},
        )
        return hit, opened
    session = mix_spotdown.session_for(run)  # analysis_unavailable when not usable: nothing taken
    # #574: a miss analyses only in a job holding one of the mix-analysis slots; without one the
    # job is requeued with it here, before the daily slot is taken or anything is called.
    run.require_analysis_slot()
    async with run.fenced() as fs:
        await mixlimit.reserve(fs)
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
    await _extend(run, FETCH_SECONDS + mix_spotdown.RELAY_SECONDS + mix_spotdown.MIX_WAIT_SECONDS)
    with private_directory() as directory:
        fetch_started = clock()
        audio = await fetch_mix_audio(run, link, directory, client_factory=client_factory)
        del link
        decode_started = clock()
        mapped = await mix_spotdown.analyse(run, session, mix_id, audio)
        timing = Timing(decode_started - fetch_started, clock() - decode_started)
    analysis = MixAnalysis(
        mix_id,
        playlist_id,
        entry.name,
        round(duration, 3),
        float(listed if listed is not None else 0.0),
        mapped.music_map,
        fingerprint(mapped.music_map),
        cached=False,
        provenance=mapped.provenance,
    )
    await store(run, opened, analysis, connection_id, timing)
    logger.info(
        "swedebeat mix analysed",
        extra={
            "job_id": str(run.lease.job_id),
            "songs": len(analysis.music_map["songs"]),
            "fetch_ms": analysis_times.to_ms(timing.fetch),
            "decode_ms": analysis_times.to_ms(timing.decode),
            "load_1m": _load_average(),
        },
    )
    return analysis, opened


_HELD_LINK = text(
    "SELECT id, playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND source = 'swedebeat' AND source_id = :mix_id"
)


async def swedebeat_step(
    run: JobRun,
    keyring: KeyRing,
    mix_id: str,
    *,
    connection_id: UUID,
    client_factory: Callable[[], httpx.AsyncClient] = mowl_media_async_client,
    index_poll_seconds: float | None = None,
    prefer: str | None = None,
) -> MusicStep:
    """The music step of a design on the user's Swedebeat mix (ADR 0016 section 3, A4): the
    analysis, then its link read under ``hold`` with the connection it was read with, as
    ``design.music`` does for a playlist. ``MusicStep.still_current`` repeats that check in the
    transaction that stores the revision (``link_source = 'swedebeat'``). The model is given the
    map only (``mix_input.mix_summary``); the name and the ids stay in ``MusicStep.mix``, with
    the map's spotdown provenance (recorded with the revision)."""
    analysis, opened = await open_swedebeat_mix(
        run,
        keyring,
        mix_id,
        connection_id=connection_id,
        client_factory=client_factory,
        index_poll_seconds=index_poll_seconds,
        prefer=prefer,
    )
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, opened):
            raise JobFailure("not_connected")
        row = (
            await fs.db.execute(
                _HELD_LINK, {"owner_id": run.lease.owner_id, "mix_id": analysis.mix_id}
            )
        ).first()
    if row is None or row.playlist_id != analysis.playlist_id:  # a reconnect deleted or moved it
        raise JobFailure("not_connected")
    m = analysis.music_map
    data = MixData({"map": m, "duration": analysis.duration}, analysis.playlist_id)
    return MusicStep(
        data=data,
        source_id=analysis.mix_id,
        link_id=row.id,
        fingerprint=analysis.fingerprint,
        unknown_bpm=(),
        _opened=opened,
        link_source=SOURCE,
        mix=MixFacts(
            mix_id=analysis.mix_id,
            name=analysis.name,
            duration=analysis.duration,
            listed_duration=analysis.listed_duration,
            unsure_beat_one=tuple(unsure_beat_one(m)),
            provenance=analysis.provenance,
        ),
    )
