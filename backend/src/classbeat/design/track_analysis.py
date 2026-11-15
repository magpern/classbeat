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
   one here, the attempt not counted; then the user's daily download slots for every miss at once
   (``tracklimit.reserve``: ``track_limit`` and nothing written when they do not fit). Then for
   each miss in order: the lease extended over the download and the analysis, a checkpoint, a
   new private directory (``mixfetch.private_directory``: 0700, under the swept work root), one
   ``download_track`` into it (60 MB cap, ``audio/mpeg``, no redirect), the analysis in the
   sandboxed child (``mixanalysis.analyse_audio`` with ``mode="track"``: seccomp required,
   Landlock when present, 1.5 GiB, 180 s, ``mowl.music.analyze_track``), and **the audio deleted
   at once**: the file right after the child, the directory with everything in it on every way
   out (a failure, a cancel, a lost lease). Then the map is stored (``track_cache.store``); a
   later failure keeps the tracks already stored, so a retry downloads only the rest.

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
from classbeat.design import tracklimit
from classbeat.design.mixanalysis import ANALYSIS_SECONDS, analyse_audio
from classbeat.design.mixfetch import private_directory
from classbeat.design.track_cache import TrackMap, cached, store
from classbeat.design.track_map_policy import MAX_TRACK_SECONDS, wanted
from classbeat.egress_gateway import DOWNLOAD_SECONDS
from classbeat.jobs import JobFailure, JobRun
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.swedebeat_gateway import client as gateway
from classbeat.swedebeat_gateway import opener
from classbeat.swedebeat_match.gateway_match import call_failure

logger = logging.getLogger(__name__)

AUDIO_NAME: Final = "track.mp3"
OWN_CATEGORIES: Final = frozenset({"track_too_large", "track_unavailable", "internal"})


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


async def _analyse_one(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, reference: str
) -> tuple[TrackMap, int | None]:
    async with run.fenced() as fs:
        await fs.extend_lease(DOWNLOAD_SECONDS + ANALYSIS_SECONDS + CALL_LEASE_MARGIN_SECONDS)
    await run.checkpoint()
    with private_directory() as directory:
        audio = directory / AUDIO_NAME
        try:
            download = await _download(run, bearer, key, reference, audio)
            result = await analyse_audio(
                run,
                audio,
                max_seconds=MAX_TRACK_SECONDS,
                map_version=track.TRACK_VERSION,
                mode="track",
            )
        finally:
            audio.unlink(missing_ok=True)  # at once; the directory goes on the way out
    return TrackMap(reference, download.sha256, result.music_map, cached=False), result.peak_rss


async def analyse_tracks(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, references: Sequence[str]
) -> list[TrackMap]:
    """Each approved track's analysis, in the order of ``references`` (deduplicated)."""
    try:
        refs = wanted(references)
    except ValueError:
        raise JobFailure("invalid_input") from None
    connection = bearer.connection
    found = await cached(run, connection, refs)
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
        await tracklimit.reserve(fs, len(misses))
    peak = 0
    for number, reference in enumerate(misses, 1):
        analysed, peak_rss = await _analyse_one(run, bearer, key, reference)
        await store(run, connection, analysed, release_slot=number == len(misses))
        found[reference] = analysed
        peak = max(peak, peak_rss or 0)
    logger.info(
        "swedebeat tracks analysed",
        extra={
            "job_id": str(run.lease.job_id),
            "tracks": len(refs),
            "downloaded": len(misses),
            "peak_rss_mb": peak // (1 << 20) if peak else None,
        },
    )
    return [found[r] for r in refs]
