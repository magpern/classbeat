"""``design_with_songs``' ``admit`` for the composer's modes (ADR 0038 sections 5, 6 and 9, #1188).

Called by ``classbeat.songs.admit`` after its checks for every mode (``song_picking_off``, the
soundless and sport rules), inside the create transaction and under the owner lock, in this order,
each refusal writing nothing:

1. ``feature_off`` unless ``composer_flow`` is on (``design.composer``);
2. the class row is locked (``chat.create_state``); ``invalid_input`` for no such class;
   ``stale_base`` unless ``base_revision_id`` is the class's **current** revision and a sketch
   (``revision_ready.is_sketch``): songs go into the sketch the user sees, never an older one;
3. ``revision_limit`` (``{limit, count}``) for a ``swap`` or ``change`` once the class's revise
   limit is used (``chat.create_state().chat_revisions`` counts their song lists too, section 9);
   a ``fill`` is **exempt**, so a user who used the limit on the sketch still gets songs; for a
   change ``chat_full`` once the chat holds its most requests;
4. the cost cap (``quota.check_cost``: ``QuotaRefused`` ``cost``, the jobs API's 409 ``quota``),
   for every mode: none takes a credit, all count toward the cost cap and the per-user limit;
5. ``not_connected``, ``consent_missing``, ``pool_busy`` (``pick_checks``): every mode writes
   the pool;
6. for a ``swap`` or ``change``, ``invalid_input`` unless the seed is the class's **newest**
   ``song_list``, made by the job being replaced (``replaces_job_id``), a composer job of this kind
   that was never approved and is ``cancelled`` (the replace hold cancelled it a moment ago, in
   this transaction), the list still hashes to its stored hash and every ``replace`` slot is in
   it. A refusal anywhere rolls the whole transaction back, the cancel included
   (``classbeat.classes.song_replace``), so the waiting job stays approvable.

A change's request is inserted as the user's chat message with the job (``after_insert``).

**``admit_composer_resume``** (ADR 0038 sections 7 and 9, #1189), called by ``admit``'s resume
rule after the receipt is found valid: a list a composer job approved is resumed **only** by the
hand-off ``classbeat.classes.song_resume`` (``receipt_invalid`` for a resume without the receipt's
sketch: ``POST /api/jobs`` cannot set it, and its job would reserve a credit), and a composer
resume only for such a list. Its sketch and ``save`` must be the receipt's (``receipt_invalid``),
the sketch must still be the class's current revision (``stale_base``: after a revise of the sketch
the receipt's base is gone), and the cost cap applies (``quota`` ``cost``). No credit.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import quota
from classbeat.context import UserContext
from classbeat.design import chat, composer, limits
from classbeat.design.revision_ready import is_sketch
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.songs import song_list
from classbeat.songs.inputs import COMPOSER_MODES, REPLACING_MODES, DesignWithSongsInput
from classbeat.songs.pick_checks import pick_checks

KIND = "design_with_songs"

# The class's newest song list and the job that made it (``admit._NEWEST_LIST`` plus the mode).
_NEWEST_LIST = text(
    "SELECT r.id, j.id AS job_id, j.kind, j.state, j.approved_at, j.input ->> 'mode' AS mode "
    "FROM class_revisions r LEFT JOIN jobs j ON j.owner_id = r.owner_id AND j.id = r.job_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'song_list' "
    "ORDER BY r.number DESC LIMIT 1"
)


async def _seed_refused(db: AsyncSession, ctx: UserContext, given: DesignWithSongsInput) -> bool:
    seed, class_id = given.seed, given.class_id
    if seed is None or class_id is None:
        return True
    row = (await db.execute(_NEWEST_LIST, {"owner_id": ctx.user_id, "class_id": class_id})).first()
    if (
        row is None
        or row.id != seed.song_list_revision_id
        or row.job_id != given.replaces_job_id
        or row.kind != KIND
        or row.mode not in COMPOSER_MODES
        or row.state != "cancelled"
        or row.approved_at is not None
    ):
        return True
    found = await song_list.load(db, ctx.user_id, class_id, seed.song_list_revision_id)
    return found is None or any(slot >= len(found.content.songs) for slot in seed.replace)


async def admit_composer(
    db: AsyncSession, ctx: UserContext, given: DesignWithSongsInput
) -> Admitted | Refused:
    """Steps 1 to 6 of the module docstring."""
    class_id, base = given.class_id, given.base_revision_id
    if class_id is None or base is None or not given.composer:
        return Refused("invalid_input")
    if not await composer.composer_on(db):
        return Refused("feature_off")
    owner = ctx.user_id
    state = await chat.create_state(db, owner, class_id, base)
    if not state.exists:
        return Refused("invalid_input")
    if state.current != base or not await is_sketch(db, owner, class_id, base):
        return Refused("stale_base")
    replacing = given.mode in REPLACING_MODES
    if replacing:
        chat_limits = limits.current()
        allowed = await chat_limits.policy.limit_for(db, ctx, class_id)
        if state.chat_revisions >= allowed:
            return Refused("revision_limit", {"limit": allowed, "count": state.chat_revisions})
        if given.mode == "change" and state.requests >= chat_limits.max_requests:
            return Refused(
                "chat_full", {"limit": chat_limits.max_requests, "count": state.requests}
            )
    await quota.check_cost(db, ctx)  # QuotaRefused ``cost``: 409 ``quota`` (ADR 0011 section 4)
    refused = await pick_checks(db, ctx, given.spotify_connection_id)
    if refused is not None:
        return refused
    if replacing and await _seed_refused(db, ctx, given):
        return Refused("invalid_input")
    if given.request is None or given.request_message_id is None:
        return Admitted()
    request, message_id = given.request, given.request_message_id

    async def after_insert(db: AsyncSession, job_id: UUID) -> None:
        try:
            await chat.insert_message(
                db,
                owner_id=owner,
                class_id=class_id,
                job_id=job_id,
                role="user",
                body=request,
                base_revision_id=base,
                message_id=message_id,
            )
        except chat.MessageRefused:
            raise RuntimeError("the job is not a job of this class") from None

    return Admitted({}, after_insert)


async def admit_composer_resume(
    db: AsyncSession,
    ctx: UserContext,
    given: DesignWithSongsInput,
    approved: Mapping[str, Any],
    save: bool,
) -> Refused | None:
    """The module docstring's ``admit_composer_resume``; None to go on with the resume rule."""
    made_by_composer = approved.get("mode") in COMPOSER_MODES
    if made_by_composer != given.composer_resume:
        return Refused("receipt_invalid")
    if not made_by_composer:
        return None
    class_id, base, owner = given.class_id, given.base_revision_id, ctx.user_id
    if class_id is None or base is None or str(base) != approved.get("base_revision_id"):
        return Refused("receipt_invalid")
    if given.save is not save:
        return Refused("receipt_invalid")
    state = await chat.create_state(db, owner, class_id, base)  # the class row, locked
    if not state.exists or state.current != base or not await is_sketch(db, owner, class_id, base):
        return Refused("stale_base")
    await quota.check_cost(db, ctx)  # QuotaRefused ``cost``: 409 ``quota`` (ADR 0011 section 4)
    return None
