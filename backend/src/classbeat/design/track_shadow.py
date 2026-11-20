"""Shadow mode for studio tracks (ADR 0040 section 9, rollout step 1; plan section 7; SD-P4 part
3, #1301): spotdown analyses the same bytes beside today's analysis, and only the comparison is
recorded. **The design keeps using today's analysis**: nothing here returns anything the track
analysis uses, and nothing here raises into it but the run's own stops.

Only while ``shadow_config.current().active`` (off by default; inactive, nothing is called or
written). For each track ``track_analysis`` downloads and analyses (a cache miss):

1. **``relay``**, while the capped download is still on disk and before it is deleted: a
   ``pending`` row (``track_shadow_rows.open_row``, the lease extended over the relay), then, in
   the owner's ``cache_scope`` (``scope_calc.cache_scope``, an HMAC of the owner id),
   ``lookup_content`` by the sha256; only on a miss the bytes are streamed to ``upload`` and the
   analysis is started (``Idempotency-Key`` from the job, the row and the step; ``client_ref`` is
   the row's random id). Nothing else leaves: no reference, title, token or URL. The relay has no
   checkpoint of its own (a stop found here only skips it), so it never stops the run before the
   track's map is stored.
2. **``finish``**, after the batch's last track is stored and its analysis slot freed: polls the
   started analyses with checkpoints between rounds (``shadow_policy.poll_delay``) within the
   job's wait budget (``shadow_policy.remaining``, about 10 min over all its runs), reads each
   ready document in the same scope, decides its timing class on path (a) and compares it with
   the local map (``shadow_record_model.evaluate``), then closes the rows in one transaction.
   Whatever is still pending is ``timed_out``.

**Every failure is an outcome, never an exception**: spotdown unreachable, a per-track status, a
bad document, the database refusing a shadow write or any other error in here is swallowed (into
the row when it can be written, else only logged by type). Only ``RunStopped`` (a cancel, a
shutdown, a lost lease at a checkpoint) and ``DatabaseRoleError`` (the worker must stop) pass, as
they do for every step of the run. Only counts are logged.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import UUID

from mowl import track

from classbeat.db import DatabaseRoleError
from classbeat.design import track_shadow_rows as rows
from classbeat.egress_spotdown import ANALYSIS_SECONDS, CALL_SECONDS, UPLOAD_SECONDS
from classbeat.jobs import JobRun, RunStopped
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.spotdown import shadow_config
from classbeat.spotdown.answer_model import Uploaded
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import AnalysisReady, Pending, TrackFailure
from classbeat.spotdown.scope_calc import cache_scope, idempotency_key
from classbeat.spotdown.shadow_policy import failure_outcome, poll_delay, remaining
from classbeat.spotdown.shadow_record_model import ShadowRecord, evaluate, local_metrics

logger = logging.getLogger(__name__)

CHUNK: Final = 256 * 1024
RELAY_SECONDS: Final = UPLOAD_SECONDS + ANALYSIS_SECONDS + CALL_SECONDS + CALL_LEASE_MARGIN_SECONDS
PASSED: Final = (RunStopped, DatabaseRoleError)


@dataclass(slots=True)
class Relayed:
    """One track in flight (memory only): the reference stays here, never in a row."""

    row_id: UUID
    scope: str
    reference: str
    sha256: str
    local_map: Mapping[str, object]
    job_id: str | None = None  # spotdown's job, while it is pending
    analysis_id: str | None = None  # ready to read
    ended: ShadowRecord | None = field(default=None)


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as audio:
        while chunk := await asyncio.to_thread(audio.read, CHUNK):
            yield chunk


async def _pause(run: JobRun, seconds: float) -> None:
    """Wait ``seconds``, or less when the worker shuts down (the next checkpoint stops the run)."""
    try:
        await asyncio.wait_for(run.shutdown.wait(), timeout=seconds)
    except TimeoutError:
        return


def _key(run: JobRun, relayed: Relayed, step: str) -> str:
    return idempotency_key("shadow", str(run.lease.job_id), str(relayed.row_id), step)


async def _send(run: JobRun, client: SpotdownClient, relayed: Relayed, audio: Path) -> None:
    found = await client.lookup_content(relayed.sha256, relayed.scope)
    if isinstance(found, AnalysisReady):
        relayed.analysis_id = found.analysis_id  # analysed before in this scope: no upload
        return
    if found is not None:
        relayed.ended = ShadowRecord(*failure_outcome(found))
        return
    uploaded = await client.upload(_chunks(audio), relayed.scope, _key(run, relayed, "upload"))
    if not isinstance(uploaded, Uploaded):
        relayed.ended = ShadowRecord(*failure_outcome(uploaded))
        return
    started = await client.start_analysis(
        relayed.scope,
        f"shadow-{relayed.row_id.hex}",
        _key(run, relayed, "start"),
        upload_id=uploaded.upload_id,
    )
    if isinstance(started, AnalysisReady):
        relayed.analysis_id = started.analysis_id
    elif isinstance(started, Pending):
        relayed.job_id = started.job_id
    else:
        relayed.ended = ShadowRecord(*failure_outcome(started))


async def relay(
    run: JobRun, reference: str, sha256: str, audio: Path, local_map: Mapping[str, object]
) -> Relayed | None:
    """Step 1 for one downloaded track; ``None`` when inactive or nothing could be recorded."""
    config = shadow_config.current()
    if not config.active or config.scope_key is None:
        return None
    try:
        scope = cache_scope(config.scope_key, run.lease.owner_id)
        row_id = await rows.open_row(run, local_metrics(local_map), RELAY_SECONDS)
    except PASSED:
        raise
    except Exception as exc:
        _swallowed("open", exc)
        return None
    relayed = Relayed(row_id, scope, reference, sha256, local_map)
    if run.lost or run.cancel_requested or run.shutdown.is_set():
        relayed.ended = ShadowRecord("pending", "stopped")  # the run's next checkpoint stops it
        return relayed
    try:
        await _send(run, SpotdownClient(config.sources), relayed, audio)
    except PASSED:
        raise
    except Exception as exc:
        _swallowed("relay", exc)
        relayed.ended = ShadowRecord("internal")
    return relayed


async def _poll(run: JobRun, client: SpotdownClient, waiting: list[Relayed]) -> None:
    now, first = await rows.first_started(run)
    deadline = time.monotonic() + remaining(first, now)
    rounds, retry_after = 0, None
    while waiting and (left := deadline - time.monotonic()) > 0:
        await _pause(run, poll_delay(rounds, retry_after, left))
        await run.checkpoint()
        rounds, retry_after = rounds + 1, None
        for relayed in list(waiting):
            outcome = await client.poll(relayed.job_id or "")
            if isinstance(outcome, Pending):
                continue
            if isinstance(outcome, TrackFailure) and outcome.status == "rate_limited":
                retry_after = max(retry_after or 0.0, outcome.retry_after_s or 0.0)
                continue
            waiting.remove(relayed)
            if isinstance(outcome, AnalysisReady):
                relayed.analysis_id = outcome.analysis_id
            else:
                relayed.ended = ShadowRecord(*failure_outcome(outcome))
    for relayed in waiting:
        relayed.ended = ShadowRecord("timed_out")


async def _read(client: SpotdownClient, relayed: Relayed) -> ShadowRecord:
    document = await client.get_analysis(relayed.analysis_id or "", relayed.scope)
    if not isinstance(document, dict):
        return ShadowRecord(*failure_outcome(document))
    return evaluate(
        document, relayed.reference, relayed.sha256, relayed.local_map, track.TRACK_VERSION
    )


async def _collect(run: JobRun, relayed: Sequence[Relayed]) -> None:
    client = SpotdownClient(shadow_config.current().sources)
    await _poll(run, client, [r for r in relayed if r.ended is None and r.job_id is not None])
    for item in relayed:
        if item.ended is None and item.analysis_id is not None:
            await run.checkpoint()
            item.ended = await _read(client, item)


async def finish(run: JobRun, relayed: Sequence[Relayed]) -> None:
    """Step 2 for the batch's relayed tracks: wait, compare, record. Never raises but the run's
    own stops."""
    if not relayed:
        return
    try:
        await _collect(run, relayed)
    except PASSED:
        raise
    except Exception as exc:
        _swallowed("collect", exc)
    records = {r.row_id: r.ended or ShadowRecord("internal") for r in relayed}
    try:
        await rows.close_rows(run, records)
    except PASSED:
        raise
    except Exception as exc:
        _swallowed("close", exc)
        return
    compared = sum(r.outcome == "compared" for r in records.values())
    extra = {"job_id": str(run.lease.job_id), "tracks": len(records), "compared": compared}
    logger.info("spotdown shadow", extra=extra)


def _swallowed(step: str, exc: Exception) -> None:
    logger.warning("spotdown shadow failed", extra={"step": step, "error_type": type(exc).__name__})
