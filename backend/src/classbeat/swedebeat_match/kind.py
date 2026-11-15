"""The ``swedebeat_match`` job kind (ADR 0016 sections 2, 5 and 8; B1; ADR 0035 sections 5 and
6): a ``song_list`` matched against SwedeBeat's catalogue through the owner's gateway, with the
user's own bearer. Deterministic: no model call, no credit, no external write.

- **Input** ``SwedebeatMatchInput``: the class, its ``song_list`` revision and the Swedebeat
  connection the page shows (``CONNECTIONS = ("swedebeat",)``: the jobs API checks it is the
  user's current one). Ids only, never a title.
- **admit** (the jobs API's create transaction): ``feature_off`` unless the effective
  ``swedebeat_enabled`` flag is on (``CLASSBEAT_SWEDEBEAT_ENABLED`` and the admin flag, stricter
  wins), then ``feature_off`` unless the user's own "I use Swedebeat" preference is on (#525; a
  connection made before the preference existed does not count as one), then ``not_connected``
  unless the Swedebeat row is ``connected`` and not past its ``expires_at`` (ADR 0035 section 5:
  a doomed job is never queued), then ``invalid_input`` for a soundless class
  (``music_kind.refuse_soundless``, #530) and unless the list is the user's ``song_list`` of that
  class and still hashes to its stored hash. One open job per class and the per-user limit are
  the jobs API's own.
- **prepare**: the flag again, from the worker's own settings and the database, and the user's
  preference (either off: the job ends ``feature_off`` and nothing is called), the list again
  (``song_list.load``), ``invalid_input`` when the class is soundless by now
  (``music_kind.still_has_music``, before any request, #470), ``app_misconfigured`` when this
  worker has no gateway URL or client key, then the bearer is opened through ``UserContext``
  (``swedebeat_account.open_bearer``: ``not_connected`` past ``expires_at``, without a request),
  in the worker only, for this job and nothing else; it must be the input's connection.
- **match** (``classbeat.swedebeat_match.gateway_match``): one gateway call for the deduplicated
  songs, title and artist only, classbeat's verdict, the 401 confirmation and the status mapping.
- **store**: the ``swedebeat_match`` revision under the list
  (``classbeat.swedebeat_match.revision``), refused ``invalid_input`` (nothing stored) when the
  class's ``music_source`` is ``none`` by then (``music_kind.check_stored``).

Registered in ``classbeat.jobs.KINDS`` on import, by the worker (which runs it) and the web app (the
jobs API validates with the same registry). Only categories are logged, never a title or the bearer.
"""

from datetime import UTC, datetime
from typing import ClassVar, Final, Self
from uuid import UUID

from pydantic import BaseModel, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.connections import find_in
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.connections.swedebeat_account import SwedebeatBearer, live, open_bearer
from classbeat.context import UserContext
from classbeat.design import music_kind
from classbeat.flags import effective as flags
from classbeat.jobs import KINDS, Complete, FencedSession, JobFailure, JobKind, JobRun
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.songs import song_list
from classbeat.swedebeat_match import config
from classbeat.swedebeat_match.gateway_match import match_songs
from classbeat.swedebeat_match.revision import KIND, MAX_SONGS, build

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
    if info is None or info.provider != PROVIDER or info.status != "connected":
        return False
    return live(info.expires_at, datetime.now(UTC))  # ADR 0035 section 5: never a doomed job


async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
    if not isinstance(parsed, SwedebeatMatchInput):
        return Refused("invalid_input")
    if not await flags.swedebeat_enabled(db, config.current().enabled):
        return Refused("feature_off")
    if not await uses_swedebeat_in(db, ctx.user_id):  # #525: nothing goes to Swedebeat
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


JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def _prepare(
    run: JobRun, given: SwedebeatMatchInput, cfg: config.MatchConfig
) -> tuple[song_list.SongListRevision, SwedebeatBearer]:
    async with run.fenced() as fs:
        enabled = await flags.swedebeat_enabled(fs.db, cfg.enabled)
        if enabled:  # #525: the user's own switch, re-read before any request
            enabled = await uses_swedebeat_in(fs.db, run.lease.owner_id)
        job_class = (await fs.db.execute(JOB_CLASS, run.lease.params())).scalar_one_or_none()
        stored = None
        if enabled and job_class == given.the_class:
            # Soundless by now: no catalogue lookup (#470, #587); the store checks again.
            await music_kind.still_has_music(fs, given.the_class)
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
    return stored, await open_match_bearer(run, cfg, given.swedebeat_connection_id)


async def open_match_bearer(
    run: JobRun, cfg: config.MatchConfig, connection_id: UUID
) -> SwedebeatBearer:
    """The bearer of the connection a match names, or the job's end before any request: no
    gateway on this worker (``app_misconfigured``), another or no connection, or one past its
    ``expires_at`` (``not_connected``). Shared with the replace's re-match."""
    if not cfg.available or cfg.keyring is None:
        raise JobFailure("app_misconfigured" if not cfg.available else "internal")
    if run.ctx.connection(PROVIDER) != connection_id:
        raise JobFailure("not_connected")
    opened = await open_bearer(run.factory, cfg.keyring, run.ctx)
    if opened.connection.id != connection_id:
        raise JobFailure("not_connected")
    return opened


async def run(job_run: JobRun) -> Complete:
    cfg = config.current()
    given = job_run.input
    if not isinstance(given, SwedebeatMatchInput) or job_run.approved:
        raise JobFailure("internal")
    await job_run.stage("prepare")
    stored, bearer = await _prepare(job_run, given, cfg)
    await job_run.stage("match")
    assert cfg.swedebeat_gateway_key is not None  # noqa: S101 - ``open_match_bearer`` checked
    songs = await match_songs(
        job_run, bearer, cfg.swedebeat_gateway_key, list(stored.content.songs)
    )
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
