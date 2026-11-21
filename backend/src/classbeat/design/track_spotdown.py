"""Studio tracks analysed by spotdown (ADR 0040 sections 7 to 9, plan sections 5, 6 and 8; SD-P5,
#1302): the source of a track's map for an allowlisted owner (``studio_tracks_config``), with the
local analysis as the fallback. Off by default; inactive, nothing here is called.

``track_analysis`` downloads a track as always (60 MB, 120 s) and, **while the capped download is
still on disk**, asks ``analyse`` for its map:

1. the owner's own cache by the bytes (``track_cache.cached_by_sha256``: the same sha256 under
   another reference) is the caller's step, before this one;
2. spotdown's lookup by sha256 in the owner's ``cache_scope`` (``scope_calc.cache_scope``, an
   HMAC of the owner id): a hit is read, **no upload**;
3. only then the bytes are streamed to ``upload`` and the analysis is started (an
   ``Idempotency-Key`` per job, track, attempt and step; ``client_ref`` a random id), and polled
   with checkpoints (``shadow_policy.poll_delay``) within the job's budget
   (``studio_policy.WAIT_SECONDS`` over every run of the job, ``track_spotdown_budget``).
   Nothing else leaves: no reference, title, token or URL.

The document is read in the same scope and goes through ``studio_map_model.studio_map``
(``timing_class_policy`` path (a), then ``spotdown_map_policy.track_map``) and
``track_map_policy.valid_track_map``; only an ``exact`` played-recording map is returned with its
provenance. **Every other outcome is ``None``**, and the caller analyses that track locally:
a per-track status after its bounded reaction (``studio_policy.reaction``), spotdown unreachable
(the rest of the job, every later run too, is then local: ``track_spotdown_budget``), the
budget spent, a refused document, or any error in here (swallowed; only its type is logged).
Only ``RunStopped`` (a cancel, a shutdown, a lost lease at a checkpoint) and ``DatabaseRoleError``
pass, as for every step of the run. Only counts and codes are logged: never a reference, a sha256
or a scope.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from mowl import track

from classbeat.db import DatabaseRoleError
from classbeat.design import track_spotdown_budget as budget
from classbeat.design.track_map_policy import valid_track_map
from classbeat.egress_spotdown import ANALYSIS_SECONDS, CALL_SECONDS, UPLOAD_SECONDS
from classbeat.jobs import JobRun, RunStopped
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.spotdown import studio_policy, studio_tracks_config
from classbeat.spotdown.answer_model import Uploaded
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import AnalysisReady, Pending, TrackFailure, Unreachable
from classbeat.spotdown.scope_calc import cache_scope, idempotency_key
from classbeat.spotdown.shadow_policy import poll_delay
from classbeat.spotdown.studio_map_model import StudioMap, studio_map

logger = logging.getLogger(__name__)

CHUNK: Final = 256 * 1024
# The lease over one track's spotdown calls (the lookup, the upload, the start); polls checkpoint.
RELAY_SECONDS: Final = UPLOAD_SECONDS + ANALYSIS_SECONDS + CALL_SECONDS + CALL_LEASE_MARGIN_SECONDS
PASSED: Final = (RunStopped, DatabaseRoleError)
TIMED_OUT: Final = "timed_out"

Failure = TrackFailure | Unreachable


@dataclass(slots=True)
class Session:
    """One run's use of spotdown; the budget and the outage are the job's (``begin``)."""

    client: SpotdownClient
    scope: str
    deadline: float | None = None  # monotonic: the end of the job's budget, once begun
    outage: bool = False
    tried: int = 0
    provider_errors: int = 0

    def left(self) -> float:
        if self.deadline is None:
            return 0.0  # not begun: nothing may be called
        return self.deadline - time.monotonic()


async def begin(run: JobRun, session: Session) -> None:
    """The job's budget and outage flag, read (or started) once per run."""
    if session.deadline is not None:
        return
    now, started, outage = await budget.open(run)
    session.deadline = time.monotonic() + studio_policy.remaining(started, now)
    session.outage = session.outage or outage


async def note_outage(run: JobRun, session: Session) -> None:
    """spotdown is unreachable: the rest of the job (this run and every later one) is local."""
    session.outage = True
    await budget.mark_outage(run)


def session_for(run: JobRun) -> Session | None:
    """The run's session when spotdown is the source for its owner, else ``None`` (local)."""
    config = studio_tracks_config.current()
    if config.scope_key is None or not config.active_for(run.lease.owner_id):
        return None
    scope = cache_scope(config.scope_key, run.lease.owner_id)
    return Session(SpotdownClient(config.sources), scope)


async def chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as audio:
        while chunk := await asyncio.to_thread(audio.read, CHUNK):
            yield chunk


async def pause(run: JobRun, seconds: float) -> None:
    """Wait ``seconds``, or less when the worker shuts down (the next checkpoint stops the run)."""
    try:
        await asyncio.wait_for(run.shutdown.wait(), timeout=seconds)
    except TimeoutError:
        return


async def wait_ready(run: JobRun, session: Session, job_id: str) -> str | Failure:
    rounds, retry_after = 0, None
    while (left := session.left()) > 0:
        await pause(run, poll_delay(rounds, retry_after, left))
        await run.checkpoint()
        rounds, retry_after = rounds + 1, None
        outcome = await session.client.poll(job_id)
        if isinstance(outcome, AnalysisReady):
            return outcome.analysis_id
        if isinstance(outcome, TrackFailure) and outcome.status == "rate_limited":
            retry_after = outcome.retry_after_s
        elif not isinstance(outcome, Pending):
            return outcome
    return TIMED_OUT


async def _attempt(
    run: JobRun, session: Session, nonce: str, attempt: int, sha256: str, audio: Path
) -> dict[str, object] | Failure | str:
    """One try: the lookup, else the upload and the start; the analysis read when ready."""
    client, scope = session.client, session.scope

    def key(step: str) -> str:
        return idempotency_key("studio", str(run.lease.job_id), nonce, str(attempt), step)

    found = await client.lookup_content(sha256, scope)
    if isinstance(found, AnalysisReady):
        analysis_id = found.analysis_id  # analysed before in this scope: no upload
    elif found is not None:
        return found
    else:
        uploaded = await client.upload(chunks(audio), scope, key("upload"))
        if not isinstance(uploaded, Uploaded):
            return uploaded
        started = await client.start_analysis(
            scope, f"studio-{uuid.uuid4().hex}", key("start"), upload_id=uploaded.upload_id
        )
        if isinstance(started, Pending):
            ready = await wait_ready(run, session, started.job_id)
            if not isinstance(ready, str) or ready == TIMED_OUT:
                return ready
            analysis_id = ready
        elif isinstance(started, AnalysisReady):
            analysis_id = started.analysis_id
        else:
            return started
    await run.checkpoint()
    return await client.get_analysis(analysis_id, scope)


async def _analyse(
    run: JobRun, session: Session, reference: str, sha256: str, audio: Path
) -> StudioMap | str:
    """The map, or why this track falls back (a code)."""
    nonce, attempt, retried = uuid.uuid4().hex, 1, False
    while session.left() > 0:
        # The keys stay the same across ``rate_limited`` waits (no second upload of the same
        # attempt); only a counted retry is a new attempt.
        outcome = await _attempt(run, session, nonce, attempt, sha256, audio)
        if isinstance(outcome, dict):
            mapped = studio_map(outcome, reference, sha256, track.TRACK_VERSION)
            if not isinstance(mapped, StudioMap):
                return mapped.reason
            return mapped if valid_track_map(mapped.track_map, track.TRACK_VERSION) else "bad_map"
        if isinstance(outcome, str):
            return outcome  # the budget is spent
        if isinstance(outcome, Unreachable):
            await note_outage(run, session)  # studio_policy.reaction: the rest of the job is local
            return "unreachable"
        step = studio_policy.reaction(outcome, retried)
        if step == "wait":
            wait = studio_policy.wait_for(outcome.retry_after_s, session.left())
            if wait is None:
                return TIMED_OUT
            await pause(run, wait)
        elif step == "retry":
            retried, attempt = True, attempt + 1
            await pause(run, min(studio_policy.RETRY_PAUSE_SECONDS, max(session.left(), 0.0)))
        else:
            session.provider_errors += outcome.status == "provider_error"
            return outcome.status
        await run.checkpoint()
    return TIMED_OUT


async def analyse(
    run: JobRun, session: Session, reference: str, sha256: str, audio: Path
) -> StudioMap | None:
    """spotdown's map of the downloaded ``audio`` for ``reference``; ``None``: analyse it
    locally."""
    try:
        await begin(run, session)
        if session.outage or session.left() <= 0:
            return None
        session.tried += 1
        result = await _analyse(run, session, reference, sha256, audio)
        if not isinstance(result, StudioMap) and studio_policy.provider_outage(
            session.provider_errors, session.tried
        ):
            await note_outage(run, session)
    except PASSED:
        raise
    except Exception as exc:
        logger.warning("spotdown studio failed", extra={"error_type": type(exc).__name__})
        return None
    if isinstance(result, StudioMap):
        return result
    logger.info(
        "spotdown studio fallback", extra={"job_id": str(run.lease.job_id), "reason": result}
    )
    return None
