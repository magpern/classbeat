"""The user's output choice for a studio draft (ADR 0036 section 6, SR C6a): the consent route's
steps, in this order, each refusal writing nothing:

1. **Pre-check** (user transactions, nothing locked): the studio on and the user's class
   (``not_found`` for another user's, before a missing connection is named), then
   ``outputs_admit.bound`` (the class's current studio draft with that hash, the choice and the
   setting's outputs, the SwedeBeat connection, the Spotify account), and ``class_busy`` while
   the class has another open job (a job of this ``Idempotency-Key`` is its replay, never busy).
   So another user's class, a stale hash or a refused choice never reaches the gateway, and a
   repeated press of a busy class asks the gateway nothing.
2. **The session check** (``outputs_session.check``): ``GET /auth/session`` with the user's bearer;
   its generation is the one the approval binds.
3. **The create**, marked with that check (``outputs_session.consenting``), through the jobs API
   (``jobs.routes.enqueue``: the owner lock, one open job per class, the per-user limit, the
   ``Idempotency-Key``: the same key and body return the first job, another body is
   ``idempotency_mismatch``), whose ``admit`` checks everything again under the locks and binds
   the generation.

The browser sends only ``{revision_id, content_hash, outputs}``: the connections are the user's
own (``ctx``), and everything an output will write comes from the stored draft.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.user import JobLimitPolicy
from classbeat.swedebeat_studio import current_draft, outputs_session
from classbeat.swedebeat_studio.admit import studio_on
from classbeat.swedebeat_studio.outputs_admit import bound
from classbeat.swedebeat_studio.outputs_input import ProduceOutputsInput
from classbeat.swedebeat_studio.outputs_kind import PRODUCE_OUTPUTS

Factory = Any

_OTHER_OPEN = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND state IN ('queued', 'running', 'waiting_for_approval') AND idempotency_key <> :key "
    "LIMIT 1"
)


@dataclass(frozen=True)
class Chosen:
    job_id: UUID | None
    created: bool
    reason: str | None = None
    counts: dict[str, int | str] = field(default_factory=dict[str, int | str])
    state: str | None = None


def _input(
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    content_hash: str,
    outputs: Sequence[str],
    base_revision_id: UUID | None = None,
) -> ProduceOutputsInput | str:
    swedebeat = ctx.connection("swedebeat")
    spotify = ctx.connection("spotify") if "spotify_playlist" in outputs else None
    if swedebeat is None or ("spotify_playlist" in outputs and spotify is None):
        return "not_connected"
    try:
        return ProduceOutputsInput.model_validate(
            {
                "class_id": str(class_id),
                "revision_id": str(revision_id),
                "content_hash": content_hash,
                "base_revision_id": None if base_revision_id is None else str(base_revision_id),
                "outputs": list(outputs),
                "swedebeat_connection_id": str(swedebeat),
                "spotify_connection_id": None if spotify is None else str(spotify),
            }
        )
    except ValidationError:
        return "invalid_input"


async def choose(
    factory: Factory,
    keyring: Any,
    ctx: UserContext,
    class_id: UUID,
    revision_id: UUID,
    content_hash: str,
    outputs: Sequence[str],
    *,
    idempotency_key: str,
    base_revision_id: UUID | None = None,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Chosen:
    require_context(ctx)
    async with user_session(factory, ctx) as db:  # the studio and the class before anything else
        if not await studio_on(db):
            return Chosen(None, False, "feature_off")
        if await current_draft.load(db, ctx.user_id, class_id) == "not_found":
            return Chosen(None, False, "not_found")
    given = _input(ctx, class_id, revision_id, content_hash, outputs, base_revision_id)
    if isinstance(given, str):
        return Chosen(None, False, given)
    params = {"owner_id": ctx.user_id, "class_id": class_id, "key": idempotency_key}
    async with user_session(factory, ctx) as db:
        done = await bound(db, ctx, given, None, lock=False)
        busy = (await db.execute(_OTHER_OPEN, params)).first() is not None
    if isinstance(done, str):
        return Chosen(None, False, done)
    if busy:
        return Chosen(None, False, "class_busy")
    checked = await outputs_session.check(factory, keyring, ctx, given.swedebeat_connection_id)
    if isinstance(checked, str):
        return Chosen(None, False, checked)
    with outputs_session.consenting(checked):
        made = await enqueue(
            factory,
            ctx,
            given,
            PRODUCE_OUTPUTS,
            idempotency_key=idempotency_key,
            ledger=ledger,
            limit=limit,
            consumes_credit=PRODUCE_OUTPUTS.consumes_credit,
        )
    return Chosen(made.job_id, made.created, made.reason, dict(made.counts), made.state)
