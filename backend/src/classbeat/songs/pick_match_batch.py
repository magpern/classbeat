"""The ``POST /v2/match`` requests of a song pick's match stage (``pick_match``; ADR 0040 section
8; SD-P6 stage A, #1303): one batch of at most 50 candidates at a time, with the reactions of
``song_match_policy``.

- A ``provider_error`` is asked again once, after ``RETRY_PAUSE_SECONDS``; a ``rate_limited``
  track (or a ``429`` for the whole request) after its clamped ``retry_after_s``, not counted as an
  attempt. At most ``MAX_PASSES`` requests per batch.
- **Every wait is charged to the job's budget before it is made** (``pick_match_budget``) and made
  only when it fits in what is left of ``WAIT_SECONDS`` over every run; what is still pending then
  keeps its last outcome (MOWL's sections).
- **Idempotency-Key**: from the job, the replacement round, the batch, the pass and the tracks
  asked (hashed), so a run that repeats a request gets spotdown's first answer, and a new pass
  (after a wait or for the retry) is a new request. ``client_ref`` is the track's index in the
  batch: no Spotify id leaves.
- spotdown unreachable (any reason) or a provider outage of a batch raise ``Fallback``.

Nothing here logs.
"""

import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.registry import Entry
from classbeat.jobs import JobRun
from classbeat.songs import pick_match_budget as budget
from classbeat.spotdown import song_match_policy as policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import MatchOutcome, TrackFailure, Unreachable
from classbeat.spotdown.request_model import ARTISTS_MAX, MATCH_BATCH_MAX, TrackQuery
from classbeat.spotdown.scope_calc import idempotency_key
from classbeat.spotdown.song_match_policy import TrackMatch

PAUSE_SLICE_SECONDS: Final = 10.0  # a wait checkpoints at least this often (a cancel, shutdown)


class Fallback(Exception):
    """Today's path for the whole class; ``reason`` is a code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Session:
    run: JobRun
    client: SpotdownClient
    spent: float  # seconds the job has waited for spotdown, over all its runs

    def left(self) -> float:
        return policy.remaining(self.spent)


async def _sleep(run: JobRun, seconds: float) -> None:
    """Wait ``seconds``, checkpointing at least every ``PAUSE_SLICE_SECONDS`` (a cancel or a
    shutdown stops the run at the next one)."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(run.shutdown.wait(), timeout=min(left, PAUSE_SLICE_SECONDS))
        await run.checkpoint()


async def wait(session: Session, seconds: float) -> None:
    """Charge ``seconds`` to the job's budget, then wait them (the analysis stage too)."""
    await budget.charge(session.run, seconds)  # before the wait: a stopped run has spent it
    session.spent += seconds
    await _sleep(session.run, seconds)
    await session.run.checkpoint()


def _query(ref: str, entry: Entry) -> TrackQuery:
    names = tuple(a.strip() for a in entry.artist.split(", ") if a.strip())[:ARTISTS_MAX]
    return TrackQuery(ref, entry.title, names or (entry.artist,), float(entry.duration_sec))


def _key(
    run: JobRun, round_no: int, batch_no: int, pass_no: int, asked: Sequence[TrackQuery]
) -> str:
    # spotdown replays a key's first answer for 24 h: a later run of the job that asks the
    # identical batch gets this run's answers again on every pass, a replayed ``rate_limited`` or
    # ``provider_error`` included, and may spend budget waiting on them (bounded by the budget and
    # ``MAX_PASSES``; those tracks then keep MOWL's sections). ADR 0040, the match-stage amendment.
    tracks = [[q.client_ref, q.title, list(q.artists), q.duration_s] for q in asked]
    digest = hashlib.sha256(json.dumps(tracks, ensure_ascii=False).encode("utf-8")).hexdigest()
    parts = (str(run.lease.job_id), f"r{round_no}", f"b{batch_no}", f"p{pass_no}", digest)
    return idempotency_key("song_match", *parts)


async def _batch(
    session: Session, asked: Sequence[TrackQuery], round_no: int, batch_no: int
) -> dict[str, MatchOutcome]:
    """Each track's final outcome after its reactions, by ``client_ref``."""
    by_ref = {q.client_ref: q for q in asked}
    pending = [q.client_ref for q in asked]
    final: dict[str, MatchOutcome] = {}
    retried: set[str] = set()
    for pass_no in range(1, policy.MAX_PASSES + 1):
        queries = tuple(by_ref[r] for r in pending)
        key = _key(session.run, round_no, batch_no, pass_no, queries)
        answer = await session.client.match(queries, idempotency_key=key)
        if isinstance(answer, Unreachable):
            raise Fallback("unreachable")
        outcomes = (
            dict(zip(pending, answer, strict=True))
            if isinstance(answer, tuple)
            else dict.fromkeys(pending, answer)  # a 429 for the whole request
        )
        again: list[str] = []
        pause = 0.0
        for ref, outcome in outcomes.items():
            final[ref] = outcome
            step = policy.reaction(outcome, ref in retried)
            if step == "retry":
                retried.add(ref)
                pause = max(pause, policy.RETRY_PAUSE_SECONDS)
            elif step == "wait" and isinstance(outcome, TrackFailure):
                pause = max(pause, outcome.retry_after_s or 0.0)
            if step != "final":
                again.append(ref)
        if not again or pass_no == policy.MAX_PASSES:
            break
        pause_s = policy.wait_for(pause, session.left())
        if pause_s is None:
            break  # what is still pending keeps its last outcome (MOWL's sections)
        await wait(session, pause_s)
        pending = again
    return final


async def match_picks(
    session: Session, picks: Sequence[PoolPick], entries: Mapping[str, Entry], round_no: int
) -> dict[str, TrackMatch]:
    """Each pick's match, by Spotify id; ``Fallback`` on a provider outage of a batch."""
    out: dict[str, TrackMatch] = {}
    for batch_no, start in enumerate(range(0, len(picks), MATCH_BATCH_MAX)):
        chunk = picks[start : start + MATCH_BATCH_MAX]
        asked = [_query(f"t{n}", entries[p.spotify_id]) for n, p in enumerate(chunk)]
        final = await _batch(session, asked, round_no, batch_no)
        errors = sum(
            isinstance(o, TrackFailure) and o.status == "provider_error" for o in final.values()
        )
        if policy.provider_outage(errors, len(chunk)):
            raise Fallback("provider_outage")
        for pick, query in zip(chunk, asked, strict=True):
            out[pick.spotify_id] = policy.track_match(final[query.client_ref])
    return out
