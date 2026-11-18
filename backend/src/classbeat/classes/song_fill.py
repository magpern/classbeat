"""The song fill hand-off (ADR 0038 section 5, #1188): ``design_with_songs`` in mode ``fill`` for
the composer's current sketch, started by the web, never by the worker (the session guard).

``start(retry=False)`` is the page's automatic call (the current revision is a sketch, no job is
queued or running and no fill was started for it); ``start(retry=True)`` is **Find songs again**
after a fill that failed, was refused, was replaced (a swap or change that failed while running)
or expired.

1. One read (no lock): the class's current revision (``not_ready`` unless it is a sketch), its
   stored brief and interpretation, the interpret job's ``music_sources`` (the attached Spotify
   playlists) and the reading's pinned songs; the user's current MOWL and Spotify connections
   (``not_connected`` without them). The input is ``fill`` with ``base_revision_id`` = the sketch.
2. The **key** = a hash of the class id, the sketch's revision id and ``attempt``
   (``fill_key``), kept on the class with the stored interpretation (``FillState``). A new sketch
   is a new key (its ``attempt`` starts at 0); **Find songs again** moves to the next ``attempt``.
3. The jobs API's create (``JobEnqueue``) with ``consumes_credit=False`` (the sketch took the
   class credit) and a ``CreateHold``: under the owner lock, the interpretation row is locked
   and read again, the sketch must still be the current revision (else the call plans again,
   once); a stored refusal of this attempt is answered as it is, a retry stores the next
   attempt (only while the class has no open job: ``class_busy``); a refusal of the create
   (``pool_busy``, ``consent_missing``, ``quota``, ``busy`` and every other but ``class_busy``,
   which only means "later") is stored on the class in that transaction. So two concurrent
   calls make one job: the second finds the first's key and gets its job (or its stored
   refusal).

No credit is used either way. Every statement filters on ``owner_id`` as well as running under RLS;
another user's class is ``not_found`` like an unknown one. Only codes and numbers are stored.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.agent.brief import Brief
from classbeat.classes.interpretation import Handoff
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import composer, interpretation_row
from classbeat.design.interpret_kind import KIND as INTERPRET_KIND
from classbeat.design.interpret_kind import InterpretRequestInput
from classbeat.design.interpretation_row import FillState, Refusal
from classbeat.design.revision_ready_policy import SKETCH
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.hold import Held
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.kinds import REASON
from classbeat.jobs.user import JobLimitPolicy
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.kind import DESIGN_WITH_SONGS

Factory = async_sessionmaker[AsyncSession]
TRANSIENT: Final = frozenset({"class_busy"})  # never stored: "try again later"
_REPLAN: Final = "_replan"  # the hold's own answer: never stored, never shown

_CLASS = text(
    "SELECT c.current_revision_id, c.brief, r.provenance ->> 'origin' AS origin, "
    "EXISTS (SELECT 1 FROM jobs j WHERE j.owner_id = c.owner_id AND j.class_id = c.id "
    "AND j.state IN ('queued', 'running', 'waiting_for_approval')) AS open "
    "FROM classes c LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
_INTERPRET_JOB = text(
    "SELECT input FROM jobs WHERE owner_id = :owner_id AND id = :job_id AND class_id = :class_id "
    "AND kind = :kind AND state = 'completed'"
)
_JOB_OF_KEY = text(
    "SELECT id, kind, state FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND idempotency_key = :key"
)


def fill_key(class_id: UUID, sketch_revision_id: UUID, attempt: int) -> str:
    """The fill job's idempotency key: one per class, sketch and attempt (at most 64 characters)."""
    digest = hashlib.sha256(
        class_id.bytes + sketch_revision_id.bytes + attempt.to_bytes(4, "big")
    ).hexdigest()
    return f"fill-{digest[:59]}"


def fill_of(row: interpretation_row.StoredInterpretation, sketch: UUID) -> FillState:
    """The stored fill of this sketch, or a fresh one (attempt 0) for a new sketch."""
    if row.fill is not None and row.fill.sketch_revision_id == sketch:
        return row.fill
    return FillState(sketch_revision_id=sketch, attempt=0)


@dataclass(frozen=True, slots=True)
class _Plan:
    class_id: UUID
    sketch: UUID
    attempt: int
    interpret_job_id: UUID


@dataclass(frozen=True, slots=True)
class _Hold:
    """The ``CreateHold`` of one fill call (``classbeat.jobs.hold``)."""

    ctx: UserContext
    plan: _Plan
    retry: bool

    def ids(self) -> dict[str, Any]:
        return {"owner_id": self.ctx.user_id, "class_id": self.plan.class_id}

    async def _row(self, db: AsyncSession) -> interpretation_row.StoredInterpretation | None:
        row = await interpretation_row.read(db, self.ctx.user_id, self.plan.class_id, lock=True)
        return row if row is not None and row.job_id == self.plan.interpret_job_id else None

    async def _store(
        self, db: AsyncSession, row: interpretation_row.StoredInterpretation, fill: FillState
    ) -> None:
        stored = row.model_copy(update={"fill": fill})
        await interpretation_row.write(db, self.ctx.user_id, self.plan.class_id, stored)

    async def before(self, db: AsyncSession, /) -> Held | None:
        row = await self._row(db)
        now = (await db.execute(_CLASS, self.ids())).first()
        if row is None or now is None:
            return Held("not_ready")
        if now.current_revision_id != self.plan.sketch or now.origin != SKETCH:
            return Held(_REPLAN)  # a new sketch meanwhile, or not a sketch any more
        fill = fill_of(row, self.plan.sketch)
        if self.retry and fill.attempt + 1 == self.plan.attempt:
            if now.open:
                return Held("class_busy")  # Find songs again once the class has no job
            await self._store(
                db, row, FillState(sketch_revision_id=self.plan.sketch, attempt=self.plan.attempt)
            )
            return None
        if fill.attempt != self.plan.attempt:
            return Held(_REPLAN)  # another call moved the attempt meanwhile
        if fill.refusal is not None:
            return Held(fill.refusal.reason, dict(fill.refusal.counts))
        if row.fill != fill:  # the first call for this sketch records it
            await self._store(db, row, fill)
        return None

    async def refused(
        self, db: AsyncSession, reason: str, counts: Mapping[str, int | str], /
    ) -> Held:
        code = reason if REASON.match(reason) else "invalid_input"
        if code in TRANSIENT:
            return Held(code, dict(counts))
        row = await self._row(db)
        if row is not None:
            kept = {k: v for k, v in counts.items() if REASON.match(k)}
            fill = fill_of(row, self.plan.sketch).model_copy(
                update={"refusal": Refusal(reason=code, counts=dict(list(kept.items())[:8]))}
            )
            await self._store(db, row, fill)
        return Held(code, dict(counts))


async def _plan(
    factory: Factory, ctx: UserContext, class_id: UUID, retry: bool
) -> tuple[_Plan, DesignWithSongsInput] | Handoff:
    owner = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        found = (await db.execute(_CLASS, owner)).first()
        if found is None:
            return Handoff(reason="not_found")
        if not await composer.composer_on(db):
            return Handoff(reason="feature_off")
        row = await interpretation_row.read(db, ctx.user_id, class_id)
        if row is None or found.origin != SKETCH or found.current_revision_id is None:
            return Handoff(reason="not_ready")
        params = {**owner, "job_id": row.job_id, "kind": INTERPRET_KIND}
        first = (await db.execute(_INTERPRET_JOB, params)).scalar_one_or_none()
    sketch = found.current_revision_id
    fill = fill_of(row, sketch)
    mowl, spotify = ctx.connection("mowl"), ctx.connection("spotify")
    if mowl is None or spotify is None:
        return Handoff(reason="not_connected")
    try:
        given = InterpretRequestInput.model_validate(first)
        parsed = DesignWithSongsInput(
            class_id=class_id,
            brief=Brief.model_validate(found.brief),
            mode="fill",
            base_revision_id=sketch,
            sources=given.music_sources,
            pinned=list(row.pinned_songs) or None,
            mowl_connection_id=mowl,
            spotify_connection_id=spotify,
        )
    except ValidationError:
        return Handoff(reason="invalid_input")
    attempt = fill.attempt + 1 if retry else fill.attempt
    return _Plan(class_id, sketch, attempt, row.job_id), parsed


async def start(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    *,
    retry: bool,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Handoff:
    """The module's hand-off: the fill job (``created`` for the call that made it) or the
    refusal (stored on the class unless transient)."""
    require_context(ctx)
    for _ in range(2):  # a second plan only when the sketch or the attempt moved meanwhile
        planned = await _plan(factory, ctx, class_id, retry)
        if isinstance(planned, Handoff):
            return planned
        plan, parsed = planned
        key = fill_key(plan.class_id, plan.sketch, plan.attempt)
        done = await enqueue(
            factory,
            ctx,
            parsed,
            DESIGN_WITH_SONGS,
            idempotency_key=key,
            ledger=ledger,
            limit=limit,
            consumes_credit=False,  # the sketch took the class credit (ADR 0038 section 9)
            hold=_Hold(ctx, plan, retry),
        )
        if done.reason == _REPLAN:
            retry = False  # a concurrent Find songs again moved the attempt: answer its job
            continue
        if done.job_id is None:
            return Handoff(reason=done.reason or "invalid_input", counts=dict(done.counts))
        return Handoff(done.job_id, DESIGN_WITH_SONGS.name, done.state, done.created)
    return Handoff(reason="class_busy")


@dataclass(frozen=True, slots=True)
class FillView:
    """What the page reads of the fill of the current sketch: the attempt, a stored refusal and
    the job of the attempt's key (none: no fill started for this sketch yet)."""

    sketch_revision_id: UUID
    attempt: int
    refusal: Refusal | None
    job: Handoff | None


async def read(factory: Factory, ctx: UserContext, class_id: UUID) -> FillView | str:
    """The view, or ``not_found`` (no such class of the user's) or ``not_ready`` (the current
    revision is no sketch, or there is no interpretation)."""
    require_context(ctx)
    owner = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        found = (await db.execute(_CLASS, owner)).first()
        if found is None:
            return "not_found"
        row = await interpretation_row.read(db, ctx.user_id, class_id)
        if row is None or found.origin != SKETCH or found.current_revision_id is None:
            return "not_ready"
        sketch = found.current_revision_id
        fill = fill_of(row, sketch)
        key = fill_key(class_id, sketch, fill.attempt)
        job = (await db.execute(_JOB_OF_KEY, {**owner, "key": key})).first()
    made = None if job is None else Handoff(job.id, job.kind, job.state)
    return FillView(sketch, fill.attempt, fill.refusal, made)
