"""Start again after a stored ``not_connected`` (ADR 0029 section 5, #904; for MA2 and MA3).

A hand-off refused ``not_connected`` (the MOWL or Spotify connection the interpret request named
is no longer the user's current one, for example after a reconnect) cannot be cleared by
``retry``: the stored interpretation read the music names of the old connection. So the way on is
a **new** ``interpret_request`` from the same request, with the user's current connections:

- only for a class of the user's whose stored interpretation has the ``not_connected`` refusal
  (else ``not_ready``; another user's class or an unknown one ``not_found``);
- the new job's input is the completed interpret job's own browser fields (the request, the music
  and sport overrides, the title), never its server fields, with the user's current MOWL
  connection (``not_connected`` when there is none) and the current Spotify one where the first
  request named one;
- it goes through the jobs API's create (``JobEnqueue``) with the kind's own ``admit``, the
  per-user limit and no credit, under an idempotency key derived from the first interpret job, so
  a second call or a reopened tab gets the same new job. It starts a new class, as every
  interpret request does; the old class is left as it is, for the page to offer or delete.

Every read filters on ``owner_id`` and runs under RLS. Only codes and ids are answered.
"""

from typing import Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.interpretation import Handoff
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import INTERPRET_REQUEST, interpretation_row
from classbeat.design.interpret_kind import InterpretRequestInput, PickChoice
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import JobEnqueue
from classbeat.jobs.user import JobLimitPolicy

Factory = async_sessionmaker[AsyncSession]
RESTARTABLE: Final = "not_connected"
BROWSER_FIELDS: Final = ("request", "music_override", "sport_override", "title")

_EXISTS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_INTERPRET_JOB = text(
    "SELECT input FROM jobs WHERE owner_id = :owner_id AND id = :job_id AND class_id = :class_id "
    "AND kind = :kind AND state = 'completed'"
)


def again_key(interpret_job_id: UUID) -> str:
    """The new interpret job's idempotency key: one per refused interpretation."""
    return f"again-{interpret_job_id.hex}"


async def interpret_again(
    factory: Factory,
    ctx: UserContext,
    class_id: UUID,
    *,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    enqueue: JobEnqueue,
) -> Handoff:
    """The module's restart: the new interpret job (``created`` for the call that made it) or a
    reason code."""
    require_context(ctx)
    owner = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        row = await interpretation_row.read(db, ctx.user_id, class_id)
        if row is None:
            found = (await db.execute(_EXISTS, owner)).first()
            return Handoff(reason="not_ready" if found is not None else "not_found")
        params = {**owner, "job_id": row.job_id, "kind": INTERPRET_REQUEST.name}
        first = (await db.execute(_INTERPRET_JOB, params)).scalar_one_or_none()
    if row.refusal is None or row.refusal.reason != RESTARTABLE or not isinstance(first, dict):
        return Handoff(reason="not_ready")
    mowl = ctx.connection("mowl")
    if mowl is None:
        return Handoff(reason="not_connected")
    try:
        given = InterpretRequestInput.model_validate(first)
        kept = given.model_dump(mode="json", include=set(BROWSER_FIELDS), exclude_none=True)
        spotify = ctx.connection("spotify") if given.spotify_connection_id else None
        if spotify is None and isinstance(given.music_override, PickChoice):
            return Handoff(reason="not_connected")  # a song pick needs Spotify
        parsed = InterpretRequestInput.model_validate(
            {
                **kept,
                "mowl_connection_id": str(mowl),
                **({"spotify_connection_id": str(spotify)} if spotify else {}),
            }
        )
    except ValidationError:
        return Handoff(reason="invalid_input")
    done = await enqueue(
        factory,
        ctx,
        parsed,
        INTERPRET_REQUEST,
        idempotency_key=again_key(row.job_id),
        ledger=ledger,
        limit=limit,
        consumes_credit=INTERPRET_REQUEST.consumes_credit,
    )
    if done.job_id is None:
        return Handoff(reason=done.reason or "invalid_input", counts=dict(done.counts))
    return Handoff(done.job_id, INTERPRET_REQUEST.name, done.state, done.created)
