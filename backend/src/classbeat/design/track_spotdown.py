"""Studio tracks analysed by spotdown (ADR 0040 sections 7 to 9, plan sections 5, 6 and 8, and its
amendment "spotdown is the only analysis path", items 1 and 4; #1302, #1353): the only source of a
track's map. Nothing falls back to a local analysis.

``track_analysis`` downloads a track as always (60 MB, 120 s) and, **while the capped download is
still on disk**, asks ``analyse`` for its map:

1. the owner's own cache by the bytes (``track_cache.cached_by_sha256``: the same sha256 under
   another reference) is the caller's step, before this one;
2. spotdown's lookup by sha256 in the studio's **shared** scope (``scope_policy.scope_for``: a
   studio track is the same file for every user, so every owner shares its analysis; the scope
   names no owner; ADR 0040 section 7, amendment of 2026-11-21): a hit is read, **no upload**;
3. only then the bytes are streamed to ``upload`` and the analysis is started (an
   ``Idempotency-Key`` per job, track, attempt and step; ``client_ref`` a random id), and polled
   with checkpoints (``shadow_policy.poll_delay``) within the attempt's budget
   (``studio_policy.WAIT_SECONDS``, ``track_spotdown_budget``: reset when a run ends in a
   retryable category). Nothing else leaves: no reference, title, token or URL.

The document is read in the same scope and goes through ``studio_map_model.studio_map``
(``timing_class_policy`` path (a), then ``spotdown_map_policy.track_map``) and
``track_map_policy.valid_track_map``; only an ``exact`` played-recording map is returned with its
provenance. **Every other outcome ends the job** (``spotdown_ending``), with the category of the
amendment's item 4: ``analysis_unavailable`` (retried) for spotdown not usable, an ``Unreachable``
about the service, a ``provider_error`` after its one retry, a ``rate_limited`` wait that does not
fit, the budget spent or an outage flag found; ``analysis_failed`` for a status about this music
(``processing_failed`` after its retry, ``alignment_failed``, ...), an ``Unreachable`` about this
request or its answer, a refused or unmappable document; ``internal`` for ``conflict``. **No error
is swallowed:** any other exception propagates (and is ``internal``), as do ``RunStopped`` (a
cancel, a shutdown, a lost lease at a checkpoint) and ``DatabaseRoleError``. The tracks already
stored are kept, so a retry analyses only the rest. Only counts and fixed codes are logged: never
a reference, a sha256 or a scope.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from mowl import track

from classbeat.design import spotdown_ending
from classbeat.design import track_spotdown_budget as budget
from classbeat.design.track_map_policy import valid_track_map
from classbeat.egress_spotdown import ANALYSIS_SECONDS, CALL_SECONDS, UPLOAD_SECONDS
from classbeat.jobs import JobFailure, JobRun
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.spotdown import outage_policy, scope_policy, studio_policy, studio_tracks_config
from classbeat.spotdown.answer_model import Uploaded
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import (
    AnalysisReady,
    Pending,
    TrackFailure,
    Unreachable,
    UnreachableReason,
)
from classbeat.spotdown.scope_calc import idempotency_key
from classbeat.spotdown.shadow_policy import poll_delay
from classbeat.spotdown.studio_map_model import StudioMap, studio_map

CHUNK: Final = 256 * 1024
# The lease over one track's spotdown calls (the lookup, the upload, the start); polls checkpoint.
RELAY_SECONDS: Final = UPLOAD_SECONDS + ANALYSIS_SECONDS + CALL_SECONDS + CALL_LEASE_MARGIN_SECONDS
TIMED_OUT: Final = "timed_out"

Failure = TrackFailure | Unreachable


@dataclass(slots=True)
class Session:
    """One run's use of spotdown; the budget and the outage are the attempt's (``begin``)."""

    client: SpotdownClient
    scope: str
    deadline: float | None = None  # monotonic: the end of the attempt's budget, once begun
    outage: bool = False

    def left(self) -> float:
        if self.deadline is None:
            return 0.0  # not begun: nothing may be called
        return self.deadline - time.monotonic()


async def begin(run: JobRun, session: Session) -> None:
    """The attempt's budget and outage flag, read (or started) once per run."""
    if session.deadline is not None:
        return
    now, started, outage = await budget.open(run)
    session.deadline = time.monotonic() + studio_policy.remaining(started, now)
    session.outage = session.outage or outage


async def note_outage(run: JobRun, session: Session) -> None:
    """spotdown is unavailable: the flag is the job's until a retry resets it (a lease-lost
    requeue keeps it, ``outage_policy``)."""
    session.outage = True
    await budget.mark_outage(run)


def session_for(run: JobRun) -> Session:
    """The run's session; ``analysis_unavailable`` (logged, nothing called) when spotdown is not
    usable in this worker (``studio_tracks_config.TracksConfig.refusal``)."""
    config = studio_tracks_config.current()
    refused = config.refusal()
    if refused is not None or config.scope_key is None:
        raise spotdown_ending.unreachable(refused or "not_configured")
    scope = scope_policy.scope_for("studio", config.scope_key, run.lease.owner_id)
    return Session(SpotdownClient(config.sources), scope)


async def unreachable(run: JobRun, session: Session, reason: UnreachableReason) -> JobFailure:
    """The job's ending for an ``Unreachable``: the outage is noted when it is the service's."""
    ending = spotdown_ending.unreachable(reason)
    if ending.category == outage_policy.UNAVAILABLE:
        await note_outage(run, session)
    return ending


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
        analysis_id = found.analysis_id  # analysed before (by any owner): no upload
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
) -> StudioMap:
    """The map, or the job's ending (``spotdown_ending``)."""
    nonce, attempt, retried = uuid.uuid4().hex, 1, False
    while session.left() > 0:
        # The keys stay the same across ``rate_limited`` waits (no second upload of the same
        # attempt); only a counted retry is a new attempt.
        outcome = await _attempt(run, session, nonce, attempt, sha256, audio)
        if isinstance(outcome, dict):
            mapped = studio_map(outcome, reference, sha256, track.TRACK_VERSION)
            if not isinstance(mapped, StudioMap):
                raise spotdown_ending.failed("map_refused")
            if not valid_track_map(mapped.track_map, track.TRACK_VERSION):
                raise spotdown_ending.failed("bad_map")
            return mapped
        if isinstance(outcome, str):  # the budget is spent
            raise spotdown_ending.unavailable("budget_spent")
        if isinstance(outcome, Unreachable):
            raise await unreachable(run, session, outcome.reason)
        step = studio_policy.reaction(outcome, retried)
        if step == "wait":
            wait = studio_policy.wait_for(outcome.retry_after_s, session.left())
            if wait is None:
                raise spotdown_ending.unavailable("wait_over_budget")
            await pause(run, wait)
        elif step == "retry":
            retried, attempt = True, attempt + 1
            await pause(run, min(studio_policy.RETRY_PAUSE_SECONDS, max(session.left(), 0.0)))
        elif step == "unavailable":
            raise spotdown_ending.unavailable(outcome.status)
        else:
            raise spotdown_ending.failed(outcome.status)
        await run.checkpoint()
    raise spotdown_ending.unavailable("budget_spent")


async def analyse(
    run: JobRun, session: Session, reference: str, sha256: str, audio: Path
) -> StudioMap:
    """spotdown's map of the downloaded ``audio`` for ``reference``, or a ``JobFailure``."""
    await begin(run, session)
    if session.outage:
        raise spotdown_ending.unavailable("outage_flag")
    if session.left() <= 0:
        raise spotdown_ending.unavailable("budget_spent")
    return await _analyse(run, session, reference, sha256, audio)
