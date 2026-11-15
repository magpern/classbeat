"""The studio's catalogue match (ADR 0036 sections 3.1 and 3.2): the facts of every candidate from
**one** gateway ``POST /catalog/match``, then the evidence retry. Never MOWL.

- **Sent**: each candidate's Spotify title and artist (``verdict_policy.sent_pair``), deduplicated
  in job memory (``gateway_match.dedupe_key``); nothing is cached across jobs, classes or users.
- **The call** is ``gateway_match.Matcher.call``: the user's bearer, the client key, the 401
  confirmation and the status mapping of the song pick's match (ADR 0035 sections 5 and 6), a
  checkpoint before each call.
- **The evidence retry** (``evidence_policy``): after a pause of ``RETRY_PAUSE_SECONDS`` the
  tracks the first answer called missing are sent once more, in one call; a track found then is
  kept. The result carries the ``Evidence``: ``suspected_unavailable`` when every sent track
  (at least 3) is still missing.
- A success records ``last_used_at`` on the connection.

Only counts are logged, never a title, an artist, a reference, the bearer or the key.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import SecretStr

from classbeat.connections import record_result
from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.jobs import JobRun
from classbeat.swedebeat_match.gateway_match import Matcher, dedupe_key
from classbeat.swedebeat_match.verdict_policy import Verdict, sent_pair, verdict
from classbeat.swedebeat_studio.evidence_policy import Evidence, evidence, merge, retry_slots

logger = logging.getLogger(__name__)

RETRY_PAUSE_SECONDS: Final = 5.0


async def _pause(seconds: float) -> None:
    """The pause before the evidence retry (tests patch it)."""
    await asyncio.sleep(seconds)


@dataclass(frozen=True)
class Matched:
    """One verdict per candidate, in order, and the match's evidence."""

    verdicts: tuple[Verdict, ...]
    evidence: Evidence


async def match_pairs(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, pairs: Sequence[tuple[str, str]]
) -> Matched:
    """The verdicts of ``pairs`` (``(title, artist)`` per candidate), see the module docstring."""
    slots: list[int | None] = []
    tracks: list[tuple[str, str]] = []
    seen: dict[tuple[str, str], int] = {}
    for title, artist in pairs:
        pair = sent_pair(title, artist)
        if pair is None:
            slots.append(None)
            continue
        slot = seen.setdefault(dedupe_key(title, artist), len(tracks))
        if slot == len(tracks):
            tracks.append(pair)
        slots.append(slot)
    first: list[Verdict] = []
    final: list[Verdict] = []
    if tracks:
        matcher = Matcher(run, bearer, key)
        first = [verdict(item) for item in await matcher.call(tracks)]
        retry = retry_slots(first)
        retried: dict[int, Verdict] = {}
        if retry:
            await _pause(RETRY_PAUSE_SECONDS)
            again = await matcher.call([tracks[slot] for slot in retry])
            retried = {slot: verdict(item) for slot, item in zip(retry, again, strict=True)}
        final = merge(first, retried)
        await record_result(run.factory, run.ctx, bearer.connection, None)
    found = evidence(first, final)
    logger.info(
        "swedebeat studio match",
        extra={
            "sent": found.sent,
            "missing_first": found.missing_first,
            "missing": found.missing,
            "state": found.state,
        },
    )
    verdicts = tuple(Verdict("missing") if slot is None else final[slot] for slot in slots)
    return Matched(verdicts, found)
