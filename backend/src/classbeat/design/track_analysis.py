"""The analysis of a studio class's approved tracks (ADR 0036 section 4.1, SR C4; ADR 0040
amendment "spotdown is the only analysis path", #1353, #1356): after the ``song_list`` approval,
each track's map from the user's own cache or from spotdown.

``analyse_tracks`` is the step a studio design job runs (C5) with the bearer it opened
(``swedebeat_match.kind.open_match_bearer``) and the gateway's client key. It never reads a flag
itself: the kind's ``admit`` and its run check them before calling it.

1. **The references** (``track_map_policy.wanted``): the approved list's catalogue references, in
   order, once each, at most 40; anything else is ``invalid_input`` before anything is read.
2. **The cache** (``track_cache.cached``, under the connection's ``hold``): the owner's rows
   (all of them spotdown's) whose provenance is this build's (``accept``). **A hit needs no
   download.** When every track is a hit nothing is called at all.
3. **The misses.** With spotdown not usable in this worker the run ends ``analysis_unavailable``
   (``track_spotdown.session_for``) before anything is requeued, counted or downloaded. A run
   without one of the worker's mix-analysis slots (#574) is requeued with one here, the attempt
   not counted; then the user's daily window must hold every miss (``tracklimit.check``:
   ``track_limit`` and nothing written when they do not fit). A run holds the slot for at most
   ``SLOT_BATCH`` downloads: the store of the last one frees it and the run is requeued for a slot
   again, so other users' analyses are not kept waiting behind 40 tracks (SR C5, the C4 review's
   carry-over); the next attempt finds the stored tracks in the cache. Then for each miss in
   order: the lease extended over the download and the relay with one daily slot reserved for it
   in the same transaction (``tracklimit.reserve``), a checkpoint, a new private directory
   (``mixfetch.private_directory``: 0700, under the swept work root), one ``download_track`` into
   it (60 MB cap, ``audio/mpeg``, no redirect), and, while the bytes are on disk, the owner's own
   cache by sha256 (the same bytes under another reference), then spotdown (``track_spotdown``:
   its lookup by sha256 in the studio's shared scope, the same for every owner (``scope_policy``,
   #1338), only then the upload and the analysis), which gives an ``exact`` map with its
   provenance. **The audio is deleted at once**: the file right after the relay, the directory with
   everything in it on every way out (a failure, a cancel, a lost lease). Then the map is stored
   (``track_cache.store``) and used as stored (jsonb's form, which the draft's ``map_hash`` is
   of); a later failure keeps the tracks already stored, so a retry downloads only the rest.

**Named failures** (job categories):
``track_too_large`` (over 60 MB, or the gateway's ``413``), ``track_unavailable`` (``404``: no file
for that reference), ``analysis_unavailable`` (retried) and ``analysis_failed`` for spotdown's
outcomes (``track_spotdown``, ``spotdown_ending``), ``track_limit``, ``not_connected``, and the
gateway's own as the match maps them (``swedebeat_match.gateway_match.call_failure``: a refused
bearer is confirmed with one session check, then ``needs_reconnect`` or a retried
``upstream_unavailable``; ``app_misconfigured``, ``rate_limited``, ``upstream_unavailable``,
``invalid_response``); a redirect is ``outbound_blocked``. Only counts are logged: never a
reference, the bearer or the key.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import SecretStr

from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.design import track_spotdown, tracklimit
from classbeat.design.mixfetch import private_directory
from classbeat.design.track_cache import (
    Stored,
    TrackMap,
    cached,
    cached_by_sha256,
    store,
)
from classbeat.design.track_map_policy import wanted
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


def accept(row: Stored) -> bool:
    """Which cached rows are hits: those whose provenance is this build's
    (``current_provenance``)."""
    return current_provenance(row.provenance, row.sha256)


async def _from_spotdown(
    run: JobRun,
    bearer: SwedebeatBearer,
    session: track_spotdown.Session,
    reference: str,
    sha256: str,
    audio: Path,
) -> TrackMap:
    """The owner's own analysis of the same bytes, else spotdown's (or a ``JobFailure``)."""
    same = await cached_by_sha256(run, bearer.connection, reference, sha256, accept)
    if same is not None:
        return TrackMap(  # no upload; stored under this reference too
            reference,
            sha256,
            same.music_map,
            cached=False,
            provenance=same.provenance,
        )
    mapped = await track_spotdown.analyse(run, session, reference, sha256, audio)
    return TrackMap(
        reference,
        sha256,
        mapped.track_map,
        cached=False,
        provenance=mapped.provenance,
    )


async def _analyse_one(
    run: JobRun,
    bearer: SwedebeatBearer,
    key: SecretStr,
    reference: str,
    session: track_spotdown.Session,
) -> TrackMap:
    async with run.fenced() as fs:
        await fs.extend_lease(
            DOWNLOAD_SECONDS + track_spotdown.RELAY_SECONDS + CALL_LEASE_MARGIN_SECONDS
        )
        await tracklimit.reserve(fs, 1)  # this download's daily slot, counted even if it fails
    await run.checkpoint()
    with private_directory() as directory:
        audio = directory / AUDIO_NAME
        try:
            download = await _download(run, bearer, key, reference, audio)
            return await _from_spotdown(run, bearer, session, reference, download.sha256, audio)
        finally:
            audio.unlink(missing_ok=True)  # at once; the directory goes on the way out


async def analyse_tracks(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, references: Sequence[str]
) -> list[TrackMap]:
    """Each approved track's analysis, in the order of ``references`` (deduplicated)."""
    try:
        refs = wanted(references)
    except ValueError:
        raise JobFailure("invalid_input") from None
    connection = bearer.connection
    found = await cached(run, connection, refs, accept)
    misses = [r for r in refs if r not in found]
    if not misses:
        if run.analysis_slot:  # requeued for a slot, then another job stored the analyses
            async with run.fenced() as fs:
                await run.release_analysis_slot(fs)
            run.slot_released()
        extra = {"job_id": str(run.lease.job_id), "tracks": len(refs)}
        logger.info("swedebeat tracks from cache", extra=extra)
        return [found[r] for r in refs]
    session = track_spotdown.session_for(run)  # analysis_unavailable when not usable: no slot yet
    run.require_analysis_slot()  # #574: requeued with a slot, before the daily slots are taken
    async with run.fenced() as fs:
        await tracklimit.check(fs, len(misses))
    batch = misses[:SLOT_BATCH]
    for number, reference in enumerate(batch, 1):
        analysed = await _analyse_one(run, bearer, key, reference, session)
        analysed = await store(run, connection, analysed, release_slot=number == len(batch))
        found[reference] = analysed  # the stored form: what a later read hashes
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
            "spotdown": len(batch),
        },
    )
    return [found[r] for r in refs]
