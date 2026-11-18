"""``studio_design``' and ``studio_revise``'s checks in the jobs API's create transaction (ADR 0036
sections 4.4 and 4.5, SR C5), after the generic ones (the class is the user's, one open job per
class, the connection ids are the user's current ones). Each refusal writes nothing.

**Both**, in order: ``feature_off`` unless the studio runs and the user's "I use Swedebeat"
preference is on; ``invalid_input`` for a class whose music is not the studio's
(``music_kind.admit_studio``); ``not_connected`` unless the SwedeBeat
row is ``connected`` and live; then the month's model cost cap (``quota.check_cost``: 409 ``quota``
``cost``), as for a chat revision.

**studio_design**: ``invalid_input`` unless the list is a studio ``song_list`` of the class approved
and completed by its own ``studio_songs`` job (C3's one approval) and still hashes; ``stale_base``
unless it is the class's newest list; ``list_has_gap``; ``connection_changed`` unless the job's
SwedeBeat connection is the list's; ``already_designed`` when a draft was made on it;
``invalid_input`` for a brief that is not cycling. It sets the
``brief`` from the approving job. **No credit** (``consumes_credit = False``): the studio pick
reserved the class's credit and committed it with the approved list (the C3 review's carry-over),
so the design never charges the class again; a design that failed may simply be started again.

**studio_revise**: as ``revise_draft``'s ``admit``: ``invalid_input`` unless the base is a studio
``draft`` of the class, ``stale_base`` unless it is current, ``revision_limit``, ``chat_full``; then
the user's ``class_messages`` row with the job and the base's hash (server fields). No credit.
"""

import uuid
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat import quota
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.context import UserContext
from classbeat.design import chat, limits, music_kind
from classbeat.design.revision_ready import revision_ready
from classbeat.jobs.kinds import Admitted, Refused
from classbeat.swedebeat_studio import design_step
from classbeat.swedebeat_studio.admit import studio_on, swedebeat_connected
from classbeat.swedebeat_studio.design_inputs import (
    StudioClassInput,
    StudioDesignInput,
    StudioReviseInput,
)
from classbeat.swedebeat_studio.song_list_model import SOURCE

_BASE_MUSIC = text(
    "SELECT music ->> 'source' AS source FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND kind = 'draft'"
)


async def _common(db: AsyncSession, ctx: UserContext, parsed: StudioClassInput) -> Refused | None:
    if not await studio_on(db) or not await uses_swedebeat_in(db, ctx.user_id):
        return Refused("feature_off")
    refused = await music_kind.admit_studio(db, ctx.user_id, parsed.the_class)
    if refused is not None:
        return refused
    if not await swedebeat_connected(db, ctx, parsed.swedebeat_connection_id):
        return Refused("not_connected")
    await quota.check_cost(db, ctx)  # QuotaRefused ``cost``: 409 ``quota`` (ADR 0011 section 4)
    return None


async def admit_design(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    if not isinstance(parsed, StudioDesignInput):
        return Refused("invalid_input")
    refused = await _common(db, ctx, parsed)
    if refused is not None:
        return refused
    class_id, list_id = parsed.the_class, parsed.song_list_revision_id
    found = await design_step.approved_list(db, ctx.user_id, class_id, list_id)
    if found is None or not found.approved or not isinstance(found.brief, dict):
        return Refused("invalid_input")
    if not found.newest:
        return Refused("stale_base")
    if found.designed:
        return Refused("already_designed")
    stored = await design_step.load_approved(db, ctx.user_id, class_id, list_id)
    if stored is None:
        return Refused("list_has_gap")
    if str(parsed.swedebeat_connection_id) != stored.music.swedebeat_connection_id:
        return Refused("connection_changed")
    brief: dict[str, Any] = found.brief  # pyright: ignore[reportUnknownVariableType]
    if brief.get("sport", "cycling") != "cycling":
        return Refused("invalid_input")
    return Admitted({"brief": brief})


async def admit_revise(db: AsyncSession, ctx: UserContext, parsed: BaseModel) -> Admitted | Refused:
    if not isinstance(parsed, StudioReviseInput):
        return Refused("invalid_input")
    class_id, base_id = parsed.the_class, parsed.base_revision_id
    not_ready = await revision_ready(db, ctx.user_id, class_id, base_id)
    if not_ready is not None:
        return Refused(not_ready)  # ADR 0038 section 4: a sketch is never a studio base
    state = await chat.create_state(db, ctx.user_id, class_id, base_id)
    if not state.exists or state.base_kind != "draft" or state.base_hash is None:
        return Refused("invalid_input")
    params = {"owner_id": ctx.user_id, "class_id": class_id, "id": base_id}
    if (await db.execute(_BASE_MUSIC, params)).scalar_one_or_none() != SOURCE:
        return Refused("invalid_input")
    if state.current != base_id:
        return Refused("stale_base")
    refused = await _common(db, ctx, parsed)
    if refused is not None:
        return refused
    chat_limits = limits.current()
    allowed = await chat_limits.policy.limit_for(db, ctx, class_id)
    if state.chat_revisions >= allowed:
        return Refused("revision_limit", {"limit": allowed, "count": state.chat_revisions})
    if state.requests >= chat_limits.max_requests:
        return Refused("chat_full", {"limit": chat_limits.max_requests, "count": state.requests})
    message_id = uuid.uuid4()

    async def after_insert(db: AsyncSession, job_id: UUID) -> None:
        try:
            await chat.insert_message(
                db,
                owner_id=ctx.user_id,
                class_id=class_id,
                job_id=job_id,
                role="user",
                body=parsed.request,
                base_revision_id=base_id,
                message_id=message_id,
            )
        except chat.MessageRefused:
            raise RuntimeError("the job is not a job of this class") from None

    return Admitted(
        {"base_revision_hash": state.base_hash.hex(), "request_message_id": message_id},
        after_insert,
    )
