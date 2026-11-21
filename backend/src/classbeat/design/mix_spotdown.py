"""A SwedeBeat mix analysed by spotdown (ADR 0040 sections 5 to 9, plan sections 5 to 8; SD-P7a,
#1304): the source of the mix's map for an allowlisted owner (``spotdown.mix_config``), with the
local analysis (``design.mixanalysis`` in the sandboxed ``mixchild``) as the fallback. Off by
default; inactive, nothing here is called.

``swedebeat_music`` fetches the mix as always (the MOWL temporary link, ``mixfetch``, 250 MB; the
link is never stored, logged or sent here) and, **while the fetched file is still on disk**, after
its own cache (``mix_cache.cached``) missed, asks ``analyse`` for the map:

1. spotdown's lookup by sha256 in the owner's ``cache_scope`` (``scope_calc.cache_scope``, an
   HMAC of the owner id): a hit is read, **no upload**;
2. only then the bytes are streamed to ``upload`` (``UPLOAD_MAX`` is the fetch's 250 MB) and the
   analysis is started (an ``Idempotency-Key`` per job, attempt and step, the same across
   ``rate_limited`` waits; ``client_ref`` a random id) and polled with checkpoints. Nothing else
   leaves: no mix id, name, link, token or URL.

The document is read in the same scope and goes through ``mix_map_model.mix_map_of``
(``timing_class_policy`` path (a), then ``mix_map_policy.mix_map``) and
``mixanalysis.valid_map``; only an ``exact`` played-recording map is returned with its provenance.

**Bounds.** The relay, the session, the per-class budget and the outage flag are the studio's
(``design.track_spotdown``, ``track_spotdown_budget``: the design job's ``spotdown_studio_budgets``
row over all its runs, ADR 0040 section 8 and its amendment of 2026-11-20); the reactions are
``studio_policy``'s. On top, a mix waits for spotdown (polls and ``rate_limited`` waits) at most
``MIX_WAIT_SECONDS``, today's 180 s limit of the local mix analysis, so a class never waits
longer for spotdown than the local analysis it falls back to may take.

**Every other outcome is ``None``**, and the caller analyses the mix locally: a status after its
bounded reaction, spotdown unreachable (the job's later runs are then local too), the budget
spent, a refused document, or an error of any type in here (swallowed; only its type is logged).
Only ``RunStopped`` and ``DatabaseRoleError`` pass, as for every step of the run. Only codes are
logged: never a mix id, a sha256, a link or a scope.
"""

import asyncio
import hashlib
import logging
import time
import uuid
from pathlib import Path
from typing import Final

from mowl import music

from classbeat.design import track_spotdown as relay
from classbeat.design.mixanalysis import ANALYSIS_SECONDS, valid_map
from classbeat.egress_spotdown import CALL_SECONDS
from classbeat.jobs import JobRun
from classbeat.spotdown import mix_config, studio_policy
from classbeat.spotdown.answer_model import Uploaded
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import AnalysisReady, Pending, Unreachable
from classbeat.spotdown.mix_map_model import MixMap, mix_map_of
from classbeat.spotdown.scope_calc import cache_scope, idempotency_key

logger = logging.getLogger(__name__)

MIX_WAIT_SECONDS: Final = ANALYSIS_SECONDS  # today's limit of the local mix analysis
# The lease over the mix's spotdown calls (the lookup, the upload, the start, the read).
RELAY_SECONDS: Final = relay.RELAY_SECONDS + CALL_SECONDS
HASH_CHUNK: Final = 1 << 20


def session_for(run: JobRun) -> relay.Session | None:
    """The run's session when spotdown is the source of mixes for its owner, else ``None``."""
    config = mix_config.current()
    if config.scope_key is None or not config.active_for(run.lease.owner_id):
        return None
    scope = cache_scope(config.scope_key, run.lease.owner_id)
    return relay.Session(SpotdownClient(config.sources), scope)


async def _attempt(
    run: JobRun, session: relay.Session, nonce: str, attempt: int, sha256: str, audio: Path
) -> dict[str, object] | relay.Failure | str:
    """One try: the lookup, else the upload and the start; the analysis read when ready."""
    client, scope = session.client, session.scope

    def key(step: str) -> str:
        return idempotency_key("mix", str(run.lease.job_id), nonce, str(attempt), step)

    found = await client.lookup_content(sha256, scope)
    if isinstance(found, AnalysisReady):
        analysis_id = found.analysis_id  # analysed before in this scope: no upload
    elif found is not None:
        return found
    else:
        uploaded = await client.upload(relay.chunks(audio), scope, key("upload"))
        if not isinstance(uploaded, Uploaded):
            return uploaded
        started = await client.start_analysis(
            scope, f"mix-{uuid.uuid4().hex}", key("start"), upload_id=uploaded.upload_id
        )
        if isinstance(started, Pending):
            ready = await relay.wait_ready(run, session, started.job_id)
            if not isinstance(ready, str) or ready == relay.TIMED_OUT:
                return ready
            analysis_id = ready
        elif isinstance(started, AnalysisReady):
            analysis_id = started.analysis_id
        else:
            return started
    await run.checkpoint()
    return await client.get_analysis(analysis_id, scope)


async def _analyse(
    run: JobRun, session: relay.Session, mix_id: str, sha256: str, audio: Path
) -> MixMap | str:
    """The map, or why the mix falls back (a code)."""
    nonce, attempt, retried = uuid.uuid4().hex, 1, False
    while session.left() > 0:
        outcome = await _attempt(run, session, nonce, attempt, sha256, audio)
        if isinstance(outcome, dict):
            mapped = mix_map_of(outcome, mix_id, sha256, music.MAP_VERSION)
            if not isinstance(mapped, MixMap):
                return mapped.reason
            return mapped if valid_map(mapped.music_map, music.MAP_VERSION) else "bad_map"
        if isinstance(outcome, str):
            return outcome  # the budget is spent
        if isinstance(outcome, Unreachable):
            await relay.note_outage(run, session)
            return "unreachable"
        step = studio_policy.reaction(outcome, retried)
        if step == "wait":
            wait = studio_policy.wait_for(outcome.retry_after_s, session.left())
            if wait is None:
                return relay.TIMED_OUT
            await relay.pause(run, wait)
        elif step == "retry":
            retried, attempt = True, attempt + 1
            pause = min(studio_policy.RETRY_PAUSE_SECONDS, max(session.left(), 0.0))
            await relay.pause(run, pause)
        else:
            return outcome.status
        await run.checkpoint()
    return relay.TIMED_OUT


def _sha256(audio: Path) -> str:
    """The hex SHA-256 of the fetched file, read in blocks (at most 250 MB)."""
    digest = hashlib.sha256()
    with audio.open("rb") as handle:
        while block := handle.read(HASH_CHUNK):
            digest.update(block)
    return digest.hexdigest()


async def analyse(run: JobRun, session: relay.Session, mix_id: str, audio: Path) -> MixMap | None:
    """spotdown's map of the fetched ``audio`` of the mix ``mix_id``; ``None``: analyse it
    locally."""
    try:
        await relay.begin(run, session)
        if session.deadline is not None:  # the job's budget, and at most today's 180 s
            session.deadline = min(session.deadline, time.monotonic() + MIX_WAIT_SECONDS)
        if session.outage or session.left() <= 0:
            return None
        sha256 = await asyncio.to_thread(_sha256, audio)
        result = await _analyse(run, session, mix_id, sha256, audio)
    except relay.PASSED:
        raise
    except Exception as exc:
        logger.warning("spotdown mix failed", extra={"error_type": type(exc).__name__})
        return None
    if isinstance(result, MixMap):
        return result
    logger.info("spotdown mix fallback", extra={"job_id": str(run.lease.job_id), "reason": result})
    return None
