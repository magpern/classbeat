"""The design-from-interpretation hand-off (ADR 0029 section 5, MA1b-3) and its read.

An ``interpret_request`` job stores ``classes.interpretation`` (``design.interpretation_row``) and
completes without enqueuing anything. ``design_from_interpretation`` then enqueues the design:

1. one read (no lock) of the stored interpretation and of the completed interpret job's input,
   which is all written once: the brief and the music become an **unchanged** ``design_draft``
   (a Spotify playlist, a Swedebeat mix or no music) or ``design_with_songs`` (a song pick) input
   for the class, with the connection ids the interpret request named;
2. the jobs API's create (``JobEnqueue``, ``classbeat.jobs.routes.enqueue``) with the kind's own
   ``admit``, the per-user limit and the class credit, under the idempotency key derived from the
   interpret job id (``interpretation_row.handoff_key``), and with a ``CreateHold``
   (``classbeat.jobs.hold``): in the create transaction, under the owner lock, the interpretation
   is read again with its row locked; a stored refusal is answered as it is (``retry`` clears it
   first and tries once more), and a refusal of the create (``consent_missing``, ``pool_busy``,
   ``busy``, ``quota`` and the rest) is stored on the class in that same transaction, its
   savepoint rolled back, so no job and no credit exist.

So two calls at the same time are serialized by the owner lock: the second sees either the job the
first created (the same key and body: the same job id and state, no second credit) or the refusal
the first stored, and answers it. A call after a reopened tab is the same call. A class whose
music could not be chosen (a running class with no MOWL playlist) stores ``needs_mowl_playlist``
the same way, under the owner lock, without a create.

Two answers are never stored (#904): a class that already has a revision and no design job of this
hand-off (designed through ``POST /api/jobs`` with its ``class_id``) is refused ``class_designed``,
so it never gets a second design and a second credit; and an ``idempotency_mismatch`` when a design
job of this key exists (an input serialized otherwise by an earlier deploy) answers that job.

Every statement filters on ``owner_id`` as well as running under RLS; another user's class is
``not_found`` like an unknown one. Only codes and whole numbers are stored or answered.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.agent.brief import Brief
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import DESIGN_DRAFT, interpretation_row
from classbeat.design.interpret_kind import KIND as INTERPRET_KIND
from classbeat.design.interpret_kind import InterpretRequestInput
from classbeat.design.interpret_music_policy import NEEDS_MOWL_PLAYLIST
from classbeat.design.interpretation_row import Refusal, StoredInterpretation
from classbeat.design.kind import DesignDraftInput
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.hold import Held
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import REASON, JobKind
from classbeat.jobs.user import JobLimitPolicy, lock_owner
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.kind import DESIGN_WITH_SONGS

Factory = async_sessionmaker[AsyncSession]
MAX_COUNTS: Final = 8

_EXISTS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_INTERPRET_JOB = text(
    "SELECT input FROM jobs WHERE owner_id = :owner_id AND id = :job_id AND class_id = :class_id "
    "AND kind = :kind AND state = 'completed'"
)
_DESIGN_JOB = text(
    "SELECT id, kind, state FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND idempotency_key = :key"
)
# A class that already has a revision and no design job of this hand-off (#904): designed some
# other way (a design job created through ``POST /api/jobs`` with its ``class_id``).
_DESIGNED = text(
    "SELECT 1 FROM classes c WHERE c.owner_id = :owner_id AND c.id = :class_id "
    "AND c.current_revision_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM jobs j "
    "WHERE j.owner_id = c.owner_id AND j.class_id = c.id AND j.idempotency_key = :key)"
)
CLASS_DESIGNED: Final = "class_designed"
MISMATCH: Final = "idempotency_mismatch"


@dataclass(frozen=True, slots=True)
class Handoff:
    """A design job (``job_id``, its ``kind`` and ``state``; ``created`` for the call that made
    it), or the refusal (``reason`` and ``counts``)."""

    job_id: UUID | None = None
    kind: str | None = None
    state: str | None = None
    created: bool = False
    reason: str | None = None
    counts: Mapping[str, int | str] = field(default_factory=dict[str, int | str])


def _counts(counts: Mapping[str, int | str]) -> dict[str, int | str]:
    """What a stored refusal keeps: named codes with whole numbers or short codes."""
    kept: dict[str, int | str] = {}
    for name, value in counts.items():
        short = isinstance(value, str) and len(value) <= 40
        if REASON.match(name) and (short or type(value) is int):
            kept[name] = value
        if len(kept) >= MAX_COUNTS:
            break
    return kept


@dataclass(frozen=True, slots=True)
class _Hold:
    """The ``CreateHold`` of one hand-off call (``classbeat.jobs.hold``)."""

    owner_id: UUID
    class_id: UUID
    job_id: UUID  # the interpret job whose interpretation this call read
    retry: bool

    async def _row(self, db: AsyncSession) -> StoredInterpretation | None:
        row = await interpretation_row.read(db, self.owner_id, self.class_id, lock=True)
        return row if row is not None and row.job_id == self.job_id else None

    def ids(self) -> dict[str, Any]:
        key = interpretation_row.handoff_key(self.job_id)
        return {"owner_id": self.owner_id, "class_id": self.class_id, "key": key}

    async def before(self, db: AsyncSession, /) -> Held | None:
        row = await self._row(db)
        if row is None:
            return Held("not_ready")
        if (await db.execute(_DESIGNED, self.ids())).first() is not None:
            return Held(CLASS_DESIGNED)  # never a second design and a second credit
        if row.refusal is not None:
            if not self.retry:
                return Held(row.refusal.reason, dict(row.refusal.counts))
            cleared = row.model_copy(update={"refusal": None})
            await interpretation_row.write(db, self.owner_id, self.class_id, cleared)
        return None

    async def refused(
        self, db: AsyncSession, reason: str, counts: Mapping[str, int | str], /
    ) -> Held:
        if reason == MISMATCH and (await db.execute(_DESIGN_JOB, self.ids())).first():
            # The design job of this key exists (its input was serialized otherwise, by an
            # earlier deploy): not a refusal to store; the caller answers that job.
            return Held(MISMATCH)
        row = await self._row(db)
        code = reason if REASON.match(reason) else "invalid_input"
        refusal = Refusal(reason=code, counts=_counts(counts))
        if row is not None:
            stored = row.model_copy(update={"refusal": refusal})
            await interpretation_row.write(db, self.owner_id, self.class_id, stored)
        return Held(refusal.reason, dict(refusal.counts))


def _design_input(
    row: StoredInterpretation, job_input: Any, class_id: UUID
) -> tuple[JobKind, JobInput] | str:
    """The design kind and its unchanged input, or the reason there is none."""
    if row.music is None:
        return NEEDS_MOWL_PLAYLIST
    if not isinstance(job_input, dict):
        return "not_ready"
    try:
        given = InterpretRequestInput.model_validate(job_input)
        brief = Brief.model_validate(row.brief)
        if row.music.get("source") == "pick":
            if given.spotify_connection_id is None:
                return "not_connected"
            songs = DesignWithSongsInput(
                class_id=class_id,
                brief=brief,
                mowl_connection_id=given.mowl_connection_id,
                spotify_connection_id=given.spotify_connection_id,
            )
            return DESIGN_WITH_SONGS, songs
        draft = DesignDraftInput.model_validate(
            {
                "class_id": class_id,
                "brief": brief,
                "music": row.music,
                "mowl_connection_id": given.mowl_connection_id,
            }
        )
        return DESIGN_DRAFT, draft
    except ValidationError:
        return "invalid_input"


def _refused(held: Held) -> Handoff:
    return Handoff(reason=held.reason, counts=dict(held.counts))


async def _store_refusal(factory: Factory, ctx: UserContext, hold: _Hold, reason: str) -> Handoff:
    """A refusal found before any create, stored under the owner lock like the create's own."""
    async with user_session(factory, ctx) as db:
        await lock_owner(db, ctx)
        early = await hold.before(db)
        if early is not None:
            return _refused(early)
        return _refused(await hold.refused(db, reason, {}))


async def design_from_interpretation(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    *,
    retry: bool,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Handoff:
    """The module's hand-off. ``not_found`` for no such class of the user's, ``not_ready`` while
    the class has no interpretation (the interpret job is queued, running or failed)."""
    require_context(ctx)
    owner = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        row = await interpretation_row.read(db, ctx.user_id, class_id)
        if row is None:
            found = (await db.execute(_EXISTS, owner)).first()
            return Handoff(reason="not_ready" if found is not None else "not_found")
        params = {**owner, "job_id": row.job_id, "kind": INTERPRET_KIND}
        job_input = (await db.execute(_INTERPRET_JOB, params)).scalar_one_or_none()
    hold = _Hold(ctx.user_id, class_id, row.job_id, retry)
    planned = _design_input(row, job_input, class_id)
    if isinstance(planned, str):
        return await _store_refusal(factory, ctx, hold, planned)
    kind, parsed = planned
    done = await enqueue(
        factory,
        ctx,
        parsed,
        kind,
        idempotency_key=interpretation_row.handoff_key(row.job_id),
        ledger=ledger,
        limit=limit,
        consumes_credit=kind.consumes_credit,
        hold=hold,
    )
    if done.job_id is None and done.reason == MISMATCH:
        return await _existing(factory, ctx, hold)
    if done.job_id is None:
        return Handoff(reason=done.reason or "invalid_input", counts=dict(done.counts))
    return Handoff(done.job_id, kind.name, done.state, done.created)


async def _existing(factory: Factory, ctx: UserContext, hold: _Hold) -> Handoff:
    """The design job of the hand-off's key, answered as a repeated call's (``idempotency_mismatch``
    with a job of that key: the job is the answer, nothing is stored)."""
    async with user_session(factory, ctx) as db:
        job = (await db.execute(_DESIGN_JOB, hold.ids())).first()
    if job is None:
        return Handoff(reason=MISMATCH)
    return Handoff(job.id, job.kind, job.state)


@dataclass(frozen=True, slots=True)
class View:
    """What the class page reads: the interpretation and the design job of the hand-off, if any
    (none and no revision: the page calls the hand-off again, after a reopened tab)."""

    interpretation: StoredInterpretation
    design: Handoff | None


async def read_view(factory: Factory, ctx: UserContext, class_id: UUID) -> View | str:
    """The view, or ``not_found`` (no such class of the user's) or ``not_ready`` (none stored)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row = await interpretation_row.read(db, ctx.user_id, class_id)
        if row is None:
            found = await db.execute(_EXISTS, {"owner_id": ctx.user_id, "class_id": class_id})
            return "not_ready" if found.first() is not None else "not_found"
        params = {
            "owner_id": ctx.user_id,
            "class_id": class_id,
            "key": interpretation_row.handoff_key(row.job_id),
        }
        job = (await db.execute(_DESIGN_JOB, params)).first()
    design = None if job is None else Handoff(job.id, job.kind, job.state)
    return View(row, design)
