"""The catalogue step of a ``swedebeat_match`` job (ADR 0035 sections 5 and 6): **one** gateway
``POST /catalog/match`` for the class's songs, with the user's bearer, and classbeat's verdict.

- **The request**: the songs in play order, deduplicated by ``dedupe_key`` (the cleaned title and
  the artist, in job memory only: nothing is cached across jobs, classes or users), each as
  ``verdict_policy.sent_pair`` (title and artist only; a song with an empty one is not sent and is
  ``missing``). One checkpoint before the call; no pacing (one call is one request in flight).
  The client is ``swedebeat_gateway.opener.open_client(MATCH_SECONDS)``, one per call.
- **The answer**: ``verdict_policy.verdict`` per song. A found or unsure song keeps the catalogue
  ``reference`` and the SwedeBeat ``bpm`` on its stored hit (ADR 0036 section 3.4; the track
  download's key, owned and deleted with the class, never a model's input).
  Every sent track ``missing`` with at least three sent is suspect: after a 5 s pause
  and a checkpoint the call is made once more; still all missing, it is stored with the operator
  line ``swedebeat match all missing`` (counts only).
- **Failures** (``confirm_policy``): a refused bearer is confirmed by one ``GET /auth/session``
  after a 5 s pause and a checkpoint, then ``needs_reconnect`` (``mark_needs_reconnect``) or a
  retried ``upstream_unavailable`` (no sooner than 330 s) with the row untouched.
  ``app_misconfigured`` (``unknown client``, a ``403``) never touches the row (no
  ``record_result``) and logs ``swedebeat gateway refused the client key``. The call's own
  ``429``, ``5xx`` and bad answers record their category on the row as ``connections.use`` does;
  a redirect is ``OutboundBlocked`` (``outbound_blocked``), the row untouched. A success records
  ``last_used_at``. ``call_failure`` is the same mapping for the studio's track download
  (``design.track_analysis``, ADR 0036 section 4.1), after its own ``track_*`` categories.

Only categories and counts are logged: never a title, an artist, the bearer or the key.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Final

from mowl.swedebeat import clean_title
from pydantic import SecretStr

from classbeat.connections import mark_needs_reconnect, record_result
from classbeat.connections.swedebeat_account import SwedebeatBearer
from classbeat.egress_gateway import MATCH_SECONDS, SESSION_SECONDS
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs.song_list import Song
from classbeat.swedebeat_gateway import client as gateway
from classbeat.swedebeat_gateway import opener
from classbeat.swedebeat_gateway.match_policy import Item
from classbeat.swedebeat_match.confirm_policy import call_failed, confirmed
from classbeat.swedebeat_match.revision import Matched, MatchedSong, Wanted
from classbeat.swedebeat_match.verdict_policy import all_missing, sent_pair, verdict

logger = logging.getLogger(__name__)

PROVIDER: Final = "swedebeat"
CONFIRM_PAUSE_SECONDS: Final = 5.0
ALL_MISSING_PAUSE_SECONDS: Final = 5.0


async def _pause(seconds: float) -> None:
    """The pause before the 401 confirmation and before the all-missing repeat (tests patch it)."""
    await asyncio.sleep(seconds)


def dedupe_key(title: str, artist: str) -> tuple[str, str]:
    """Two songs with this key are one sent track (memory only)."""
    return clean_title(title).casefold(), artist.strip().casefold()


class _Match:
    """One job's match: its run, the opened bearer and the client key."""

    def __init__(
        self, run: JobRun, bearer: SwedebeatBearer, key: SecretStr, what: str = "match"
    ) -> None:
        self.run, self.bearer, self.key, self.what = run, bearer, key, what

    async def confirm(self) -> tuple[str, int | None]:
        """ADR 0035 section 5: pause, then one session check with the same bearer and key."""
        await _pause(CONFIRM_PAUSE_SECONDS)
        await self.run.checkpoint()
        check: tuple[str, int | None] | None = None
        try:
            async with opener.open_client(SESSION_SECONDS) as client:
                await gateway.session(client, self.key, self.bearer.bearer)
        except gateway.GatewayFailure as refused:
            check = (refused.category, refused.retry_after)
        return confirmed(check)

    async def fail(self, category: str, retry_after: int | None) -> JobFailure:
        """The job failure for a failed match call, after the connection's rules."""
        own = category != "bearer_refused"  # the call's own answer, not a confirmed one
        if own:
            category, retry_after = call_failed(category, retry_after)
        else:
            category, retry_after = await self.confirm()
        run, opened = self.run, self.bearer.connection
        if category == "app_misconfigured":  # the row and its ``last_error`` stay as they are
            logger.error("swedebeat gateway refused the client key", extra={"category": category})
        elif category == "needs_reconnect":
            await mark_needs_reconnect(run.factory, run.ctx, opened)
        elif own:
            await record_result(run.factory, run.ctx, opened, category)
        message = "swedebeat match failed" if self.what == "match" else "swedebeat download failed"
        logger.info(message, extra={"category": category})
        return JobFailure(category, retry_after, provider=PROVIDER)

    async def call(self, tracks: Sequence[tuple[str, str]]) -> list[Item]:
        await self.run.checkpoint()
        category, retry_after = "invalid_response", None
        try:
            async with opener.open_client(MATCH_SECONDS) as client:
                return await gateway.match(client, self.key, self.bearer.bearer, tracks)
        except gateway.GatewayFailure as refused:
            category, retry_after = refused.category, refused.retry_after
        raise await self.fail(category, retry_after)  # outside the except block: nothing chained

    async def answer(self, tracks: Sequence[tuple[str, str]]) -> list[Item]:
        items = await self.call(tracks)
        if not all_missing(items):
            return items
        await _pause(ALL_MISSING_PAUSE_SECONDS)  # a SwedeBeat outage looks like "all missing"
        items = await self.call(tracks)
        if all_missing(items):
            logger.warning("swedebeat match all missing", extra={"sent": len(tracks)})
        return items


async def call_failure(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, category: str, retry_after: int | None
) -> JobFailure:
    """The job failure for a failed gateway call other than the match (the track download):
    the same confirmation of a refused bearer and the same connection rules."""
    return await _Match(run, bearer, key, what="download").fail(category, retry_after)


async def match_songs(
    run: JobRun, bearer: SwedebeatBearer, key: SecretStr, songs: Sequence[Song]
) -> list[MatchedSong]:
    """Each song's verdict, in play order (see the module docstring)."""
    slots: list[int | None] = []
    tracks: list[tuple[str, str]] = []
    seen: dict[tuple[str, str], int] = {}
    for song in songs:
        pair = sent_pair(song.title, song.artist)
        if pair is None:
            slots.append(None)
            continue
        slot = seen.setdefault(dedupe_key(song.title, song.artist), len(tracks))
        if slot == len(tracks):
            tracks.append(pair)
        slots.append(slot)
    items = await _Match(run, bearer, key).answer(tracks) if tracks else []
    matched: list[MatchedSong] = []
    for position, (song, slot) in enumerate(zip(songs, slots, strict=True), 1):
        found = verdict(None if slot is None else items[slot])
        hit = None
        if found.status != "missing" and found.title is not None and found.artist is not None:
            hit = Matched(
                title=found.title,
                artist=found.artist,
                duration=found.duration,
                reference=found.reference,
                bpm=found.bpm,
            )
        matched.append(
            MatchedSong(
                position=position,
                status=found.status,
                wanted=Wanted(title=song.title, artist=song.artist),
                swedebeat=hit,
            )
        )
    if tracks:
        await record_result(run.factory, run.ctx, bearer.connection, None)
    return matched
