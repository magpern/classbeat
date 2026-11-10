"""The signed-in user's Swedebeat hand-off (ADR 0016 section 7).

One ``user_session`` per step. The flag is the route's, before this is called. ``match_revision_id``
is the stored approval when it belongs to the current draft's song list (``stored_approval``);
a request never supplies it. A row whose ``expires_at`` has passed is refused and never revived.
A soundless class (``music_source`` ``none``) has no hand-off: every step that reads or opens a
row answers ``invalid_input`` (ADR 0007 amendment of 2026-11-03, item 9; #530).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from mowl import api as mowl_api
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.agent.brief import Brief
from classbeat.classes.selection import stored_approval
from classbeat.connections import ConnectionFailure, open_connection, run_library_call
from classbeat.context import UserContext, require_context
from classbeat.crypto.keks import KeyRing
from classbeat.db import user_session
from classbeat.design import music_kind
from classbeat.design.kind import DESIGN_DRAFT, DesignDraftInput, SwedebeatMusicChoice
from classbeat.egress import OutboundBlocked
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.kinds import JobKind
from classbeat.jobs.user import JobLimitPolicy
from classbeat.operations.swedebeat import parse_mixes
from classbeat.swedebeat_handoff.kind import VERIFY_MIX, VerifyInput
from classbeat.swedebeat_handoff.rows import (
    Handoff,
    insert_open,
    live,
    mark_verified,
    supersede_stale,
)

Factory = async_sessionmaker[AsyncSession]
_CLASS = text(
    "SELECT brief, music_source, current_revision_id FROM classes "
    "WHERE owner_id = :owner_id AND id = :class_id"
)
_EXISTS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")


@dataclass(frozen=True, slots=True)
class Step:
    """``reason`` is set when the call did not produce a row. ``needs_snapshot`` asks the route
    to list mixes and call ``open_from`` (the row is not usable as it stands)."""

    handoff: Handoff | None = None
    reason: str | None = None
    needs_snapshot: bool = False
    job_id: UUID | None = None
    created: bool = False
    counts: Mapping[str, int | str] = field(default_factory=dict)
    dismissed: bool = False
    revision_id: UUID | None = None  # a design that is already the class's current one


def _params(owner: UUID, class_id: UUID) -> dict[str, UUID]:
    return {"owner_id": owner, "class_id": class_id}


async def _bound_row(
    db: AsyncSession, owner: UUID, class_id: UUID
) -> tuple[Handoff | None, Step | None]:
    """The live row when the stored match still belongs to the current song list.

    A ``Step`` is the refusal (a soundless class, no row, expired, or the match does not
    belong). None for the second value means the caller may snapshot.
    """
    soundless = await music_kind.refuse_soundless(db, owner, class_id)  # #530
    if soundless is not None:
        return None, Step(reason=soundless.reason)
    bound, reason = await stored_approval(db, owner, class_id)
    if bound is None:
        return None, Step(reason=reason or "stale_match")
    row = await live(db, owner, class_id)
    if row is None:
        return None, None
    if row.expired:
        return None, Step(reason="handoff_expired")
    if row.match_revision_id != bound.revision_id:
        return None, None  # a new approval: the caller snapshots a new row
    return row, None


async def read(factory: Factory, ctx: UserContext, class_id: UUID) -> Step:
    """The live hand-off. Expired rows are refused; nothing is revived."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(_EXISTS, _params(ctx.user_id, class_id))).first() is None:
            return Step(reason="not_found")
        row, refused = await _bound_row(db, ctx.user_id, class_id)
        if refused is not None and refused.reason == "handoff_expired":
            return refused
        if row is None:
            return refused or Step(reason="not_found")
        return Step(handoff=row)


async def begin(factory: Factory, ctx: UserContext, class_id: UUID) -> Step:
    """The live row for this approval, or ``needs_snapshot`` when a new one has to be taken.
    No provider call."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row, refused = await _bound_row(db, ctx.user_id, class_id)
        if row is not None:
            return Step(handoff=row)
        if refused is not None and refused.reason != "handoff_expired":
            return refused
        # No row, a new approval, or an expired one: the step offers a new snapshot.
        return Step(needs_snapshot=True)


async def open_from(factory: Factory, ctx: UserContext, class_id: UUID, mix_ids: list[str]) -> Step:
    """Store the snapshot. Re-checks the approval in the same transaction; an expired or
    replaced row is superseded, never updated."""
    require_context(ctx)
    owner = ctx.user_id
    try:
        async with user_session(factory, ctx) as db:
            soundless = await music_kind.refuse_soundless(db, owner, class_id)  # #530
            if soundless is not None:
                return Step(reason=soundless.reason)
            bound, reason = await stored_approval(db, owner, class_id)
            if bound is None:
                return Step(reason=reason or "stale_match")
            await supersede_stale(db, owner, class_id, bound.revision_id)
            row = await insert_open(db, owner, class_id, bound.revision_id, mix_ids)
            return Step(handoff=row)
    except IntegrityError:
        async with user_session(factory, ctx) as db:
            row = await live(db, owner, class_id)
        return Step(handoff=row) if row is not None else Step(reason="class_busy")


async def list_mix_ids(
    factory: Factory, keyring: KeyRing, ctx: UserContext
) -> tuple[list[str] | None, str | None]:
    """The user's mix ids through MOWL. The reason is a connection category, or
    ``outbound_blocked``."""
    require_context(ctx)
    try:
        opened = await open_connection(factory, keyring, ctx, "mowl")
        creds = opened.credentials
        answer = await run_library_call(lambda: mowl_api.swedebeat_mixes(creds), sign_in=False)
    except ConnectionFailure as failure:
        return None, failure.category
    except OutboundBlocked:
        return None, "outbound_blocked"
    return [entry.mix_id for entry in parse_mixes(answer)], None


async def _enqueue(
    factory: Factory,
    ctx: UserContext,
    parsed: JobInput,
    kind: JobKind,
    *,
    idempotency_key: str,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    consumes_credit: bool,
    enqueue: JobEnqueue,
) -> Step:
    """``enqueue`` is ``classbeat.jobs.routes.enqueue`` (the jobs API's create)."""
    created = await enqueue(
        factory,
        ctx,
        parsed,
        kind,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        consumes_credit=consumes_credit,
    )
    if created.job_id is None:
        return Step(reason=created.reason or "invalid_input", counts=created.counts)
    return Step(job_id=created.job_id, created=created.created)


async def press(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    mix_id: str | None,
    *,
    idempotency_key: str,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Step:
    """ "I made the mix" / "Check again". A verified row is a 200 with no job."""
    require_context(ctx)
    connection = ctx.connection("mowl")
    if connection is None:
        return Step(reason="not_connected")
    async with user_session(factory, ctx) as db:
        row, refused = await _bound_row(db, ctx.user_id, class_id)
        if row is not None and row.state == "verified":
            return Step(handoff=row)
        if refused is not None:
            return refused
        if row is None:
            return Step(reason="not_found")
    try:
        parsed = VerifyInput(class_id=class_id, mowl_connection_id=connection, mix_id=mix_id)
    except ValidationError:
        return Step(reason="invalid_input")
    return await _enqueue(
        factory,
        ctx,
        parsed,
        VERIFY_MIX,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        consumes_credit=False,
        enqueue=enqueue,
    )


async def override(
    factory: Factory, keyring: KeyRing, ctx: UserContext, class_id: UUID, mix_id: str
) -> Step:
    """ "This is the right mix": the analysed mix, still in the user's listing. No new fetch
    of audio. An expired row is refused before the listing. The soundless check and the stored
    approval are checked again in the transaction that marks the row verified. Same owner is not
    enough."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row, refused = await _bound_row(db, ctx.user_id, class_id)
        if refused is not None and row is None:
            return refused
        if row is None:
            return Step(reason="not_found")
        if row.state == "verified":
            if row.mix_id == mix_id:
                return Step(handoff=row)
            return Step(reason="invalid_input")
        if row.verdict not in ("too_short", "too_long") or row.mix_id != mix_id:
            return Step(reason="not_checked")
        handoff_id = row.id
    ids, listing_reason = await list_mix_ids(factory, keyring, ctx)
    if listing_reason is not None or ids is None:
        return Step(reason=listing_reason or "upstream_unavailable")
    if mix_id not in ids:
        return Step(reason="invalid_input")
    async with user_session(factory, ctx) as db:
        # The class may have turned soundless during the listing: checked again here, as in
        # ``open_from``, so its row is never marked verified (#530, #589).
        soundless = await music_kind.refuse_soundless(db, ctx.user_id, class_id)
        if soundless is not None:
            return Step(reason=soundless.reason)
        bound, reason = await stored_approval(db, ctx.user_id, class_id)
        current = await live(db, ctx.user_id, class_id)
        if (
            bound is None
            or current is None
            or current.id != handoff_id
            or current.match_revision_id != bound.revision_id
        ):
            if current is not None and current.expired:
                return Step(reason="handoff_expired")
            if current is not None and current.state == "verified" and current.mix_id == mix_id:
                return Step(handoff=current)
            return Step(reason=reason or "stale_match")
        if not await mark_verified(db, ctx.user_id, handoff_id, mix_id):
            fresh = await live(db, ctx.user_id, class_id)
            if fresh is not None and fresh.expired:
                return Step(reason="handoff_expired")
            if fresh is not None and fresh.state == "verified" and fresh.mix_id == mix_id:
                return Step(handoff=fresh)
            return Step(reason="not_checked")
        current = await live(db, ctx.user_id, class_id)
    return Step(handoff=current)


async def prepare_design(factory: Factory, ctx: UserContext, class_id: UUID) -> Step:
    """What a design on the verified mix needs, or the reason it cannot start. No job yet.
    A class already designed on this mix is a no-op (``revision_id`` set)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        row, refused = await _bound_row(db, ctx.user_id, class_id)
        if row is None:
            return refused or Step(reason="not_found")
        if row.state != "verified" or row.mix_id is None:
            return Step(reason="not_checked")
        found = (await db.execute(_CLASS, _params(ctx.user_id, class_id))).first()
        if found is None:
            return Step(reason="not_found")
        source = found.music_source if isinstance(found.music_source, dict) else {}
        if (
            source.get("source") == "mowl_swedebeat_mix"
            and source.get("mix_id") == row.mix_id
            and found.current_revision_id is not None
        ):
            return Step(handoff=row, revision_id=found.current_revision_id)
        return Step(handoff=row)


async def start_design(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    *,
    idempotency_key: str,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Step:
    """Queue ``design_draft`` on the verified mix (A4). The job carries the hand-off id."""
    ready = await prepare_design(factory, ctx, class_id)
    if ready.reason is not None or ready.revision_id is not None or ready.handoff is None:
        return ready
    mix_id = ready.handoff.mix_id
    connection = ctx.connection("mowl")
    if mix_id is None or connection is None:
        return Step(reason="not_checked" if mix_id is None else "not_connected")
    async with user_session(factory, ctx) as db:
        found = (await db.execute(_CLASS, _params(ctx.user_id, class_id))).first()
    if found is None or not isinstance(found.brief, dict):
        return Step(reason="invalid_input")
    try:
        brief = Brief.model_validate(found.brief)
        parsed = DesignDraftInput(
            class_id=class_id,
            brief=brief,
            music=SwedebeatMusicChoice(source="mowl_swedebeat_mix", mix_id=mix_id),
            mowl_connection_id=connection,
            handoff_id=ready.handoff.id,
        )
    except ValidationError:
        return Step(reason="invalid_input")
    queued = await _enqueue(
        factory,
        ctx,
        parsed,
        DESIGN_DRAFT,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        consumes_credit=True,
        enqueue=enqueue,
    )
    return queued


async def not_now(factory: Factory, ctx: UserContext, class_id: UUID) -> Step:
    """The other button. Writes nothing. An expired row is still refused."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if (await db.execute(_EXISTS, _params(ctx.user_id, class_id))).first() is None:
            return Step(reason="not_found")
        row = await live(db, ctx.user_id, class_id)
        if row is not None and row.expired:
            return Step(reason="handoff_expired")
        if row is None:
            return Step(dismissed=True)
        return Step(handoff=row, dismissed=True)
