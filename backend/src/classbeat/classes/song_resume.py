"""**Create the playlist again** for a composer class (ADR 0038 section 7, ADR 0008 section 6;
#1189): a resume of a failed phase 2, made from the **approval receipt** alone.

``resume`` reads the class's newest ``song_list`` revision and the job that stored it; that job
must be a composer job (``fill``, ``swap`` or ``change``) the user approved (``approved_at``), else
``receipt_invalid``. The input is ``design_with_songs`` in mode ``resume`` naming **that** list,
with the receipt's brief, its sketch as ``base_revision_id`` and its ``save`` (``approval_save``),
plus the user's current connections. Nothing of it comes from the browser: the route takes no body
(whatever is sent is ignored), so a browser-supplied list, hash, base or ``save`` changes nothing.

It is created through the jobs API's create (``JobEnqueue``) with **no credit** (the sketch took
the class credit, section 9) under the browser's ``Idempotency-Key``; ``admit``'s resume rule then
checks the receipt (``songs.admit.receipt_valid``: still the newest list, approved, ended without
completing, no other resume in flight), the sketch still current (``stale_base``), the account and
the unresolved-write rule. Every read filters on ``owner_id`` and runs under RLS; another user's
class is ``not_found`` like an unknown one.
"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.interpretation import Handoff
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import composer
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.user import JobLimitPolicy
from classbeat.songs.inputs import COMPOSER_MODES, DesignWithSongsInput, Resume
from classbeat.songs.kind import DESIGN_WITH_SONGS

Factory = async_sessionmaker[AsyncSession]
_EXISTS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
# The class's newest song list and the job that stored it (the receipt, when it approved it).
_RECEIPT = text(
    "SELECT r.id, j.input, j.approval_save, j.approved_at, j.approval_revision_id "
    "FROM class_revisions r JOIN jobs j ON j.owner_id = r.owner_id AND j.id = r.job_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'song_list' "
    "AND j.kind = 'design_with_songs' ORDER BY r.number DESC LIMIT 1"
)


async def resume(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    *,
    idempotency_key: str,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Handoff:
    require_context(ctx)
    ids = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        if (await db.execute(_EXISTS, ids)).first() is None:
            return Handoff(reason="not_found")
        if not await composer.composer_on(db):
            return Handoff(reason="feature_off")
        row = (await db.execute(_RECEIPT, ids)).first()
    given = row.input if row is not None and isinstance(row.input, dict) else None
    if (
        row is None
        or given is None
        or row.approved_at is None
        or row.approval_revision_id != row.id
        or given.get("mode") not in COMPOSER_MODES  # pyright: ignore[reportUnknownMemberType]
    ):
        return Handoff(reason="receipt_invalid")
    mowl, spotify = ctx.connection("mowl"), ctx.connection("spotify")
    if mowl is None or spotify is None:
        return Handoff(reason="not_connected")
    try:
        parsed = DesignWithSongsInput(
            class_id=class_id,
            brief=given["brief"],
            mode="resume",
            resume=Resume(song_list_revision_id=row.id),
            base_revision_id=given["base_revision_id"],
            save=bool(row.approval_save),
            mowl_connection_id=mowl,
            spotify_connection_id=spotify,
        )
    except (ValidationError, KeyError):
        return Handoff(reason="receipt_invalid")
    done = await enqueue(
        factory,
        ctx,
        parsed,
        DESIGN_WITH_SONGS,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        consumes_credit=False,  # ADR 0038 section 9: the sketch committed the class credit
    )
    if done.job_id is None:
        return Handoff(reason=done.reason or "invalid_input", counts=dict(done.counts))
    return Handoff(done.job_id, DESIGN_WITH_SONGS.name, done.state, done.created)
