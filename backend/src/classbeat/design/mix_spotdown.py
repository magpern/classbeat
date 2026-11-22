"""A SwedeBeat mix analysed by spotdown (ADR 0040 sections 5 to 9, plan sections 5 to 8; SD-P7a,
#1304; its amendment "spotdown is the only analysis path", #1353): the only source of the mix's
map. Nothing falls back to a local analysis.

``swedebeat_music`` fetches the mix as always (the MOWL temporary link, ``mixfetch``, 250 MB; the
link is never stored, logged or sent here) and, **while the fetched file is still on disk**, after
its own cache (``mix_cache.cached``) missed, asks ``analyse`` for the map:

1. spotdown's lookup by sha256 in the owner's ``cache_scope`` (``scope_policy.scope_for``: a mix
   is never shared, so an HMAC of the owner id): a hit is read, **no upload**;
2. only then the bytes are streamed to ``upload`` (``UPLOAD_MAX`` is the fetch's 250 MB) and the
   analysis is started (an ``Idempotency-Key`` per job, attempt and step, the same across
   ``rate_limited`` waits; ``client_ref`` a random id) and polled with checkpoints. Nothing else
   leaves: no mix id, name, link, token or URL.

The document is read in the same scope and goes through ``mix_map_model.mix_map_of``
(``timing_class_policy`` path (a), then ``mix_map_policy.mix_map``) and
``mixanalysis.valid_map``; only an ``exact`` played-recording map is returned with its provenance.

**Bounds.** The relay, the session, the attempt's budget and the outage flag are the studio's
(``design.track_spotdown``, ``track_spotdown_budget``: the design job's ``spotdown_studio_budgets``
row, reset when a run ends in a retryable category, ADR 0040 section 8 and its amendment "spotdown
is the only analysis path", item 4); the reactions are ``studio_policy``'s. On top, a mix waits for
spotdown (polls and ``rate_limited`` waits) at most ``MIX_WAIT_SECONDS`` (180 s).

**Every other outcome ends the job** (``spotdown_ending``): ``analysis_unavailable`` (retried) for
spotdown not usable, an ``Unreachable`` about the service, a ``provider_error`` after its one
retry, a wait that does not fit, the budget or the 180 s cap spent; ``analysis_failed`` for a
status about this music, an ``Unreachable`` about this request or its answer, a refused document;
``internal`` for ``conflict``. **No error is swallowed**; ``RunStopped`` and ``DatabaseRoleError``
pass as for every step of the run. Only fixed codes are logged: never a mix id, a sha256, a link
or a scope.
"""

import asyncio
import hashlib
import time
import uuid
from pathlib import Path
from typing import Final

from mowl import music

from classbeat.design import spotdown_ending
from classbeat.design import track_spotdown as relay
from classbeat.design.mixanalysis import valid_map
from classbeat.egress_spotdown import CALL_SECONDS
from classbeat.jobs import JobRun
from classbeat.spotdown import scope_policy, studio_policy, studio_tracks_config
from classbeat.spotdown.answer_model import Uploaded
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import AnalysisReady, Pending, Unreachable
from classbeat.spotdown.mix_map_model import MixMap, mix_map_of
from classbeat.spotdown.scope_calc import idempotency_key

MIX_WAIT_SECONDS: Final = 180.0  # at most this long waiting for spotdown, polls and waits
# The lease over the mix's spotdown calls (the lookup, the upload, the start, the read).
RELAY_SECONDS: Final = relay.RELAY_SECONDS + CALL_SECONDS
HASH_CHUNK: Final = 1 << 20


def session_for(run: JobRun) -> relay.Session:
    """The run's session; ``analysis_unavailable`` (logged, nothing called) when spotdown is not
    usable in this worker (``studio_tracks_config.TracksConfig.refusal``)."""
    config = studio_tracks_config.current()
    refused = config.refusal()
    if refused is not None or config.scope_key is None:
        raise spotdown_ending.unreachable(refused or "not_configured")
    scope = scope_policy.scope_for("mix", config.scope_key, run.lease.owner_id)
    return relay.Session(SpotdownClient(), scope)


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
) -> MixMap:
    """The map, or the job's ending (``spotdown_ending``)."""
    nonce, attempt, retried = uuid.uuid4().hex, 1, False
    while session.left() > 0:
        outcome = await _attempt(run, session, nonce, attempt, sha256, audio)
        if isinstance(outcome, dict):
            mapped = mix_map_of(outcome, mix_id, sha256, music.MAP_VERSION)
            if not isinstance(mapped, MixMap):
                raise spotdown_ending.failed("map_refused")
            if not valid_map(mapped.music_map, music.MAP_VERSION):
                raise spotdown_ending.failed("bad_map")
            return mapped
        if isinstance(outcome, str):  # the budget is spent
            raise spotdown_ending.unavailable("budget_spent")
        if isinstance(outcome, Unreachable):
            raise await relay.unreachable(run, session, outcome.reason)
        step = studio_policy.reaction(outcome, retried)
        if step == "wait":
            wait = studio_policy.wait_for(outcome.retry_after_s, session.left())
            if wait is None:
                raise spotdown_ending.unavailable("wait_over_budget")
            await relay.pause(run, wait)
        elif step == "retry":
            retried, attempt = True, attempt + 1
            pause = min(studio_policy.RETRY_PAUSE_SECONDS, max(session.left(), 0.0))
            await relay.pause(run, pause)
        elif step == "unavailable":
            raise spotdown_ending.unavailable(outcome.status)
        else:
            raise spotdown_ending.failed(outcome.status)
        await run.checkpoint()
    raise spotdown_ending.unavailable("budget_spent")


def _sha256(audio: Path) -> str:
    """The hex SHA-256 of the fetched file, read in blocks (at most 250 MB)."""
    digest = hashlib.sha256()
    with audio.open("rb") as handle:
        while block := handle.read(HASH_CHUNK):
            digest.update(block)
    return digest.hexdigest()


async def analyse(run: JobRun, session: relay.Session, mix_id: str, audio: Path) -> MixMap:
    """spotdown's map of the fetched ``audio`` of the mix ``mix_id``, or a ``JobFailure``."""
    await relay.begin(run, session)
    if session.deadline is not None:  # the attempt's budget, and at most 180 s
        session.deadline = min(session.deadline, time.monotonic() + MIX_WAIT_SECONDS)
    if session.outage:
        raise spotdown_ending.unavailable("outage_flag")
    if session.left() <= 0:
        raise spotdown_ending.unavailable("budget_spent")
    sha256 = await asyncio.to_thread(_sha256, audio)
    return await _analyse(run, session, mix_id, sha256, audio)
