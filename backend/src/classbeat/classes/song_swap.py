"""Swap songs of the composer's waiting list (ADR 0038 section 6, #1188).

``swap`` reads the class's live waiting composer job (``song_replace.WAITING_SQL``; the named list
must be the one it waits on, ``not_waiting`` otherwise) and creates ``design_with_songs`` in mode
``swap``: the same sketch as ``base_revision_id``, ``seed`` = that list and the slots to replace,
the attached playlists again, the waiting job as ``replaces_job_id``, the user's current
connections, **no credit**. The create replaces the waiting job in one transaction
(``song_replace.ReplaceHold``): a refusal rolls the cancel back, so the list stays approvable.
The idempotency key is the browser's. Every read filters on ``owner_id`` and runs under RLS.
"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.interpretation import Handoff
from classbeat.classes.song_replace import WAITING_SQL, ReplaceHold, Rollback, waiting_parts
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import composer
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.user import JobLimitPolicy
from classbeat.songs.inputs import DesignWithSongsInput, Seed
from classbeat.songs.kind import DESIGN_WITH_SONGS

Factory = async_sessionmaker[AsyncSession]


async def swap(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    list_id: UUID,
    replace: list[int],
    *,
    idempotency_key: str,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Handoff:
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await composer.composer_on(db):
            return Handoff(reason="feature_off")
        row = (
            await db.execute(WAITING_SQL, {"owner_id": ctx.user_id, "class_id": class_id})
        ).first()
    parts = None if row is None else waiting_parts(row.input)
    if row is None or parts is None or row.approval_revision_id != list_id:
        return Handoff(reason="not_waiting")
    base, stored = parts
    mowl, spotify = ctx.connection("mowl"), ctx.connection("spotify")
    if mowl is None or spotify is None:
        return Handoff(reason="not_connected")
    try:
        parsed = DesignWithSongsInput(
            class_id=class_id,
            brief=stored["brief"],
            mode="swap",
            base_revision_id=base,
            replaces_job_id=row.id,
            seed=Seed(song_list_revision_id=list_id, replace=replace),
            sources=stored.get("sources"),
            mowl_connection_id=mowl,
            spotify_connection_id=spotify,
        )
    except (ValidationError, KeyError):
        return Handoff(reason="invalid_input")
    try:
        done = await enqueue(
            factory,
            ctx,
            parsed,
            DESIGN_WITH_SONGS,
            idempotency_key=idempotency_key,
            ledger=ledger,
            limit=limit,
            consumes_credit=False,  # ADR 0038 section 9
            hold=ReplaceHold(ctx, class_id, row.id, list_id, base, idempotency_key),
        )
    except Rollback as refused:
        return Handoff(reason=refused.reason, counts=refused.counts)
    if done.job_id is None:
        return Handoff(reason=done.reason or "invalid_input", counts=dict(done.counts))
    return Handoff(done.job_id, DESIGN_WITH_SONGS.name, done.state, done.created)
