"""The analysis of a studio class's approved tracks (ADR 0036 section 4.1, SR C4): after the
``song_list`` approval, each track's map from the user's own cache or from its audio.

``analyse_tracks`` is the step a studio design job runs (C5) with the bearer it opened
(``swedebeat_match.kind.open_match_bearer``) and the gateway's client key. It never reads a flag
itself: the kind's ``admit`` and its run check them before calling it.

1. **The references** (``track_map_policy.wanted``): the approved list's catalogue references, in
   order, once each, at most 40; anything else is ``invalid_input`` before anything is read.
2. **The cache** (``track_cache.cached``, under the connection's ``hold``). **A hit needs no
   download.** When every track is a hit nothing is called at all.
3. **The misses.** A run without one of the worker's mix-analysis slots (#574) is requeued with
   one here, the attempt not counted; then the user's daily window must hold every miss
   (``tracklimit.check``: ``track_limit`` and nothing written when they do not fit). A run holds
   the slot for at most ``SLOT_BATCH`` downloads: the store of the last one frees it and the run
   is requeued for a slot again, so other users' analyses are not kept waiting behind 40 tracks
   (SR C5, the C4 review's carry-over); the next attempt finds the stored tracks in the cache.
   Then for each miss in order: the lease extended over the download and the analysis with one
   daily slot reserved for it in the same transaction (``tracklimit.reserve``), a checkpoint, a
   new private directory (``mixfetch.private_directory``: 0700, under the swept work root), one
   ``download_track`` into it (60 MB cap, ``audio/mpeg``, no redirect), the analysis in the
   sandboxed child (``mixanalysis.analyse_audio`` with ``mode="track"``: seccomp required,
   Landlock when present, 1.5 GiB, 180 s, ``mowl.music.analyze_track``), and **the audio deleted
   at once**: the file right after the child, the directory with everything in it on every way
   out (a failure, a cancel, a lost lease). Then the map is stored (``track_cache.store``); a
   later failure keeps the tracks already stored, so a retry downloads only the rest.
4. **spotdown as the source** (``track_spotdown``, ADR 0040 sections 7 and 9; SD-P5, off by
   default, only for the allowlisted owners of ``studio_tracks_config``): the cache's hits are
   then also the owner's ``spotdown`` rows whose provenance is current, and for each downloaded
   track, while its bytes are on disk, the owner's own cache by sha256 (the same bytes under
   another reference) and then spotdown (its lookup by sha256 in the owner's scope, only then the
   upload and the analysis) give an ``exact`` map with its provenance; **on any spotdown failure
   or outage that track is analysed locally** as in step 3, and only that track changes. Off, a
   ``spotdown`` row is a miss and nothing here differs from a local run.
5. **Shadow mode** (``track_shadow``, ADR 0040 section 9; off by default, and never while spotdown
   is the source): each downloaded track's bytes are also relayed to spotdown before they are
   deleted, and once the batch is stored (its slot freed) the comparisons are awaited within
   their budget and recorded. It never changes a map or a result and never fails the step.

**Named failures** (job categories): ``track_too_large`` (over 60 MB, or the gateway's ``413``),
``track_unavailable`` (``404``: no file for that reference), ``analysis_failed`` (the sandbox
did not hold, the child's limits, undecodable or too long audio, an answer that is not a track
map), ``track_limit``, ``not_connected``, and the gateway's own as the match maps them
(``swedebeat_match.gateway_match.call_failure``: a refused bearer is confirmed with one session
check, then ``needs_reconnect`` or a retried ``upstream_unavailable``; ``app_misconfigured``,
``rate_limited``, ``upstream_unavailable``, ``invalid_response``); a redirect is
``outbound_blocked``. Only counts are logged: never a reference, the bearer or the key.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from mowl import track
from pydantic import SecretStr

from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.design import track_shadow, track_spotdown, tracklimit
from classbeat.design.mixanalysis import ANALYSIS_SECONDS, analyse_audio
from classbeat.design.mixfetch import private_directory
from classbeat.design.track_cache import (
    Accept,
    Stored,
    TrackMap,
    cached,
    cached_by_sha256,
    store,
)
from classbeat.design.track_map_policy import MAX_TRACK_SECONDS, wanted
from classbeat.egress_gateway import DOWNLOAD_SECONDS
from classbeat.jobs import JobFailure, JobRun
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.spotdown.studio_map_model import current_provenance
from classbeat.swedebeat_gateway import client as gateway
from classbeat.swedebeat_gateway import opener
from classbeat.swedebeat_match.gateway_match import call_failure

logger = logging.getLogger(__name__)

AUDIO_NAME: Final = "track.mp3"
OWN_CATEGORIES: Final = frozenset({"track_too_large", "track_unavailable", "internal"})
SLOT_BATCH: Final = 4  # downloads per hold of an analysis slot (about 4 x 5 min at worst)


async def _download(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, reference: str, audio: Path
) -> gateway.Download:
    category, retry_after = "invalid_response", None
    try:
        async with opener.open_client(DOWNLOAD_SECONDS) as client:
            return await gateway.download_track(client, key, bearer.bearer, reference, audio)
    except gateway.GatewayFailure as refused:
        category, retry_after = refused.category, refused.retry_after
    except ValueError:  # a reference or a destination refused before anything was sent
        category = "internal"
    if category in OWN_CATEGORIES:
        raise JobFailure(category)
    raise await call_failure(run, bearer, key, category, retry_after)  # nothing chained


def accept(spotdown: bool) -> Accept:
    """Which cached rows are hits: ``local`` ones always, ``spotdown`` ones only while spotdown
    is the owner's source and their provenance is this build's (``current_provenance``)."""

    def accepted(row: Stored) -> bool:
        if row.analyser == "local":
            return True
        return (
            spotdown
            and row.analyser == "spotdown"
            and current_provenance(row.provenance, row.sha256)
        )

    return accepted


async def _from_spotdown(
    run: JobRun,
    bearer: SwedebeatBearer,
    session: track_spotdown.Session,
    reference: str,
    sha256: str,
    audio: Path,
) -> TrackMap | None:
    """The owner's own analysis of the same bytes, else spotdown's; ``None``: analyse locally."""
    same = await cached_by_sha256(run, bearer.connection, reference, sha256, accept(True))
    if same is not None:
        return TrackMap(  # no upload; stored under this reference too
            reference,
            sha256,
            same.music_map,
            cached=False,
            analyser=same.analyser,
            provenance=same.provenance,
        )
    mapped = await track_spotdown.analyse(run, session, reference, sha256, audio)
    if mapped is None:
        return None
    return TrackMap(
        reference,
        sha256,
        mapped.track_map,
        cached=False,
        analyser="spotdown",
        provenance=mapped.provenance,
    )


async def _analyse_one(
    run: JobRun,
    bearer: SwedebeatBearer,
    key: SecretStr,
    reference: str,
    session: track_spotdown.Session | None,
) -> tuple[TrackMap, int | None, track_shadow.Relayed | None]:
    relay = 0.0 if session is None else track_spotdown.RELAY_SECONDS
    async with run.fenced() as fs:
        await fs.extend_lease(
            DOWNLOAD_SECONDS + relay + ANALYSIS_SECONDS + CALL_LEASE_MARGIN_SECONDS
        )
        await tracklimit.reserve(fs, 1)  # this download's daily slot, counted even if it fails
    await run.checkpoint()
    with private_directory() as directory:
        audio = directory / AUDIO_NAME
        try:
            download = await _download(run, bearer, key, reference, audio)
            if session is not None:
                found = await _from_spotdown(
                    run, bearer, session, reference, download.sha256, audio
                )
                if found is not None:
                    return found, None, None  # the audio goes in ``finally``, as below
            result = await analyse_audio(
                run,
                audio,
                max_seconds=MAX_TRACK_SECONDS,
                map_version=track.TRACK_VERSION,
                mode="track",
            )
            # Shadow mode (off by default): the same bytes to spotdown, before they are deleted;
            # never while spotdown is the source (this track fell back to the local analysis).
            shadow = None
            if session is None:
                shadow = await track_shadow.relay(
                    run, reference, download.sha256, audio, result.music_map
                )
        finally:
            audio.unlink(missing_ok=True)  # at once; the directory goes on the way out
    analysed = TrackMap(reference, download.sha256, result.music_map, cached=False)
    return analysed, result.peak_rss, shadow


async def analyse_tracks(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, references: Sequence[str]
) -> list[TrackMap]:
    """Each approved track's analysis, in the order of ``references`` (deduplicated)."""
    try:
        refs = wanted(references)
    except ValueError:
        raise JobFailure("invalid_input") from None
    connection = bearer.connection
    session = track_spotdown.session_for(run)  # None (the default): today's local analysis
    found = await cached(run, connection, refs, accept(session is not None))
    misses = [r for r in refs if r not in found]
    if not misses:
        if run.analysis_slot:  # requeued for a slot, then another job stored the analyses
            async with run.fenced() as fs:
                await run.release_analysis_slot(fs)
            run.slot_released()
        extra = {"job_id": str(run.lease.job_id), "tracks": len(refs)}
        logger.info("swedebeat tracks from cache", extra=extra)
        return [found[r] for r in refs]
    run.require_analysis_slot()  # #574: requeued with a slot, before the daily slots are taken
    async with run.fenced() as fs:
        await tracklimit.check(fs, len(misses))
    batch = misses[:SLOT_BATCH]
    peak, shadows, by_spotdown = 0, list[track_shadow.Relayed](), 0
    for number, reference in enumerate(batch, 1):
        analysed, peak_rss, shadow = await _analyse_one(run, bearer, key, reference, session)
        await store(run, connection, analysed, release_slot=number == len(batch))
        found[reference] = analysed
        peak = max(peak, peak_rss or 0)
        by_spotdown += analysed.analyser == "spotdown"
        shadows += [shadow] if shadow is not None else []
    await track_shadow.finish(run, shadows)  # the slot is free; never changes ``found``
    if len(batch) < len(misses):  # the slot is free: requeued for one, the rest from the cache
        logger.info(
            "swedebeat tracks batch analysed",
            extra={"job_id": str(run.lease.job_id), "downloaded": len(batch)},
        )
        run.require_analysis_slot()
    logger.info(
        "swedebeat tracks analysed",
        extra={
            "job_id": str(run.lease.job_id),
            "tracks": len(refs),
            "downloaded": len(misses),
            "spotdown": by_spotdown if session is not None else None,
            "peak_rss_mb": peak // (1 << 20) if peak else None,
        },
    )
    return [found[r] for r in refs]
