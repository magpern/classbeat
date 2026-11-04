"""The ``swedebeat_match`` job kind (ADR 0016 sections 2, 5 and 8; B1): a ``song_list`` matched
against Swedebeat's catalogue with the user's own Swedebeat token. Deterministic: no model call,
no credit, no external write.

- **Input** ``SwedebeatMatchInput``: the class, its ``song_list`` revision and the Swedebeat
  connection the page shows (``CONNECTIONS = ("swedebeat",)``: the jobs API checks it is the
  user's current one). Ids only, never a title.
- **admit** (the jobs API's create transaction): ``feature_off`` unless the effective
  ``swedebeat_enabled`` flag is on (``CLASSBEAT_SWEDEBEAT_ENABLED`` and the admin flag, stricter
  wins), then ``not_connected`` unless the Swedebeat row is ``connected`` (a row that needs a
  reconnect makes no call), then ``invalid_input`` for a soundless class
  (``music_kind.refuse_soundless``, #530) and unless the list is the user's ``song_list`` of
  that class and still hashes to its stored hash. One open job per class and the per-user limit
  are the jobs API's own.
- **prepare**: the flag again, from the worker's own settings and the database (off: the job ends
  ``feature_off`` and nothing is called), the list again (``song_list.load``), then the Swedebeat
  connection is opened through ``UserContext`` (``classbeat.connections``), in the worker only, for
  this job and nothing else; it must be the input's.
- **match**: for each song in play order, at most ``MAX_SONGS``, one catalogue request
  (``classbeat.swedebeat_match.search``) with only its title and artist, one at a time, paced
  (``pace_seconds`` between two requests), a checkpoint before each. Two songs with the same
  cleaned title and artist share one request (in-job memory only: nothing is cached across jobs,
  classes or users, ADR 0016 section 5). The token follows ADR 0015: a 401 or 403 flips the
  connection to ``needs_reconnect`` (the job ends so); a 429 ends the attempt ``rate_limited``
  with Swedebeat's ``Retry-After`` (the retry waits at least that, and the job sentence names
  that wait); a 5xx or a timeout is ``upstream_unavailable`` (retried); success records
  ``last_used_at``.
- **store**: the ``swedebeat_match`` revision under the list
  (``classbeat.swedebeat_match.revision``), refused ``invalid_input`` (nothing stored) when the
  class's ``music_source`` is ``none`` by then (``music_kind.check_stored``).

Registered in ``classbeat.jobs.KINDS`` on import, by the worker (which runs it) and the web app (the
jobs API validates with the same registry). Only categories are logged, never a title or the token.
"""

import asyncio
import logging
from typing import Any, ClassVar, Final, Self
from uuid import UUID

import httpx
from pydantic import BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import egress
from classbeat.connections import (
    OpenConnection,
    find_in,
    mark_needs_reconnect,
    open_connection,
    record_result,
)
from classbeat.context import UserContext
from classbeat.design import music_kind
from classbeat.flags import effective as flags
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.songs import song_list
from classbeat.swedebeat_match import config, search
from classbeat.swedebeat_match.catalogue import Hit, dedupe_key, verdict
from classbeat.swedebeat_match.revision import KIND, MAX_SONGS, Matched, MatchedSong, Wanted, build

logger = logging.getLogger(__name__)


async def _pause(seconds: float) -> None:
    """The gap between two catalogue requests of one job (one request in flight)."""
    await asyncio.sleep(seconds)


PROVIDER: Final = "swedebeat"
STAGES: Final = ("prepare", "match")


class SwedebeatMatchInput(JobInput):
    """A match: the class, its ``song_list`` revision and the user's Swedebeat connection."""

    CONNECTIONS: ClassVar[tuple[str, ...]] = (PROVIDER,)

    song_list_revision_id: UUID
    swedebeat_connection_id: UUID

    @model_validator(mode="after")
    def _names_its_class(self) -> Self:
        if self.class_id is None:
            raise ValueError("a match names its class")
        return self

    @property
    def the_class(self) -> UUID:
        assert self.class_id is not None  # noqa: S101 - the validator above
        return self.class_id


async def _connected(db: AsyncSession, ctx: UserContext, connection_id: UUID) -> bool:
    if ctx.connection(PROVIDER) != connection_id:
        return False
    info = await find_in(db, ctx, connection_id)
    return info is not None and info.provider == PROVIDER and info.status == "connected"


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
    if not isinstance(parsed, SwedebeatMatchInput):
        return Refused("invalid_input")
    if not await flags.swedebeat_enabled(db, config.current().enabled):
        return Refused("feature_off")
    if not await _connected(db, ctx, parsed.swedebeat_connection_id):
        return Refused("not_connected")
    soundless = await music_kind.refuse_soundless(db, ctx.user_id, parsed.the_class)  # #530
    if soundless is not None:
        return soundless
    stored = await song_list.load(db, ctx.user_id, parsed.the_class, parsed.song_list_revision_id)
    if stored is None or len(stored.content.songs) > MAX_SONGS:
        return Refused("invalid_input")
    return Admitted()


def catalogue_client() -> httpx.AsyncClient:
    """The catalogue client of one job. The seam the tests replace (the fake catalogue over a fake
    transport, ``egress.swedebeat_cloud_test_client``); production builds the worker's own."""
    return egress.swedebeat_cloud_async_client()


JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def _prepare(
    run: JobRun, given: SwedebeatMatchInput, cfg: config.MatchConfig
) -> tuple[song_list.SongListRevision, OpenConnection]:
    async with run.fenced() as fs:
        enabled = await flags.swedebeat_enabled(fs.db, cfg.enabled)
        job_class = (await fs.db.execute(JOB_CLASS, run.lease.params())).scalar_one_or_none()
        stored = None
        if enabled:
            stored = await song_list.load(
                fs.db, run.lease.owner_id, given.the_class, given.song_list_revision_id
            )
    if not enabled:
        raise JobFailure("feature_off")
    if cfg.keyring is None or job_class != given.the_class:
        raise JobFailure("internal")  # the API stores the input's class: a bug, never a match
    if stored is None or len(stored.content.songs) > MAX_SONGS:
        raise JobFailure("invalid_input")
    if run.ctx.connection(PROVIDER) != given.swedebeat_connection_id:
        raise JobFailure("not_connected")
    opened = await open_connection(run.factory, cfg.keyring, run.ctx, PROVIDER)
    if opened.id != given.swedebeat_connection_id:
        raise JobFailure("not_connected")
    return stored, opened


def _token(opened: OpenConnection) -> str:
    token: Any = opened.credentials.swedebeat_token
    if not isinstance(token, str) or not token:
        raise JobFailure("not_connected")
    return token


async def _search(
    run: JobRun, opened: OpenConnection, client: httpx.AsyncClient, title: str, artist: str
) -> list[Hit]:
    failure: search.SearchFailure | None = None
    try:
        return await search.search(client, _token(opened), title, artist)
    except search.SearchFailure as refused:
        failure = refused
    # Outside the except block: nothing chained. The connection's rules of ``connections.use``.
    if failure.category == "needs_reconnect":
        await mark_needs_reconnect(run.factory, run.ctx, opened)
    else:
        await record_result(run.factory, run.ctx, opened, failure.category)
    logger.info("swedebeat search failed", extra={"category": failure.category})
    raise JobFailure(failure.category, failure.retry_after, provider=PROVIDER)


async def match(
    run: JobRun,
    opened: OpenConnection,
    client: httpx.AsyncClient,
    songs: list[song_list.Song],
    pace_seconds: float,
) -> list[MatchedSong]:
    """Each song's verdict, in play order (see the module docstring)."""
    asked: dict[tuple[str, str], list[Hit]] = {}
    matched: list[MatchedSong] = []
    for position, song in enumerate(songs, 1):
        key = dedupe_key(song.title, song.artist)
        hits = asked.get(key)
        if hits is None:
            if asked and pace_seconds > 0:
                await _pause(pace_seconds)
            await run.checkpoint()
            hits = await _search(run, opened, client, song.title, song.artist)
            asked[key] = hits
        found = verdict(song.title, hits)
        hit = found.hit
        matched.append(
            MatchedSong(
                position=position,
                status=found.status,
                wanted=Wanted(title=song.title, artist=song.artist),
                swedebeat=None
                if hit is None
                else Matched(title=hit.title, artist=hit.artist, duration=hit.duration),
            )
        )
    await record_result(run.factory, run.ctx, opened, None)
    return matched


async def run(job_run: JobRun) -> Complete:
    cfg = config.current()
    given = job_run.input
    if not isinstance(given, SwedebeatMatchInput) or job_run.approved:
        raise JobFailure("internal")
    await job_run.stage("prepare")
    stored, opened = await _prepare(job_run, given, cfg)
    await job_run.stage("match")
    async with catalogue_client() as client:
        songs = await match(job_run, opened, client, list(stored.content.songs), cfg.pace_seconds)
    revision = build(
        songs, song_list_id=given.song_list_revision_id, song_list_hash=stored.content_hash
    )

    async def store(fs: FencedSession) -> UUID:
        await music_kind.check_stored(fs, soundless=False)  # never for a soundless class (#530)
        return await revision.store(fs)

    return Complete(store)


SWEDEBEAT_MATCH: Final = KINDS.register(
    JobKind(KIND, SwedebeatMatchInput, STAGES, run, consumes_credit=False, admit=admit)
)
