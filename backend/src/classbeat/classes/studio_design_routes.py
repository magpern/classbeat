"""``POST /api/classes/{class_id}/studio-songs/{revision_id}/design``: start the design of a studio
class on its approved song list (ADR 0036 section 4, SR C5; ``swedebeat_studio.design_kind``).

The body is ``{}``: everything else is the user's own rows. The ``studio_design`` job is made from
the approving ``studio_songs`` job's SwedeBeat connection (the brief is ``admit``'s, from the same
job), through the jobs API's create (``jobs.routes.enqueue``: the owner lock, one open job per
class, the per-user limit, the ``Idempotency-Key``) and ``design_admit.admit_design``. It consumes
**no credit**: the studio pick committed the class's.

201 with ``{job_id, kind, state}``, 200 with the same job for a repeated ``Idempotency-Key``; 409
``{reason}`` with the refusal; 404 ``not_found`` for another user's or an unknown class or list,
and with the studio off. Signed in (``auth.require_user``) and, like every unsafe request, with
the CSRF token the auth middleware checks.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text

from classbeat import auth
from classbeat.classes.handoff_routes import job_enqueue
from classbeat.classes.studio_songs_routes import StudioJobOut, _refuse, _uuid  # pyright: ignore[reportPrivateUsage]
from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import IDEMPOTENCY_KEY, credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy
from classbeat.swedebeat_studio.admit import KIND as SONGS_KIND
from classbeat.swedebeat_studio.admit import studio_on
from classbeat.swedebeat_studio.design_inputs import StudioDesignInput
from classbeat.swedebeat_studio.design_kind import KIND, STUDIO_DESIGN

router = APIRouter(prefix="/api/classes", tags=["classes"])

_LIST_JOB = text(
    "SELECT j.input FROM class_revisions r JOIN jobs j ON j.owner_id = r.owner_id "
    "AND j.id = r.job_id WHERE r.owner_id = :owner_id AND r.class_id = :class_id "
    "AND r.id = :revision_id AND r.kind = 'song_list' AND j.kind = :kind"
)


class EmptyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post(
    "/{class_id}/studio-songs/{revision_id}/design",
    status_code=201,
    response_model=StudioJobOut,
    responses={200: {"model": StudioJobOut}, 400: {}, 404: {}, 409: {}, 422: {}, 429: {}},
)
async def design_studio_class(
    class_id: str,
    revision_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> StudioJobOut | JSONResponse:
    wanted, revision = _uuid(class_id), _uuid(revision_id)
    if wanted is None or revision is None:
        return _refuse("not_found")
    if idempotency_key is None:
        return _refuse("idempotency_key_missing")
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return _refuse("idempotency_key_invalid")
    try:
        EmptyIn.model_validate_json((await request.body()) or b"{}")
    except ValidationError:
        return _refuse("invalid_input")
    factory = request.app.state.session_factory
    params = {"owner_id": ctx.user_id, "class_id": wanted, "revision_id": revision}
    async with user_session(factory, ctx) as db:
        if not await studio_on(db):
            return _refuse("feature_off")
        made: Any = (await db.execute(_LIST_JOB, {**params, "kind": SONGS_KIND})).scalar()
    if not isinstance(made, dict):
        return _refuse("not_found")
    try:
        parsed = StudioDesignInput.model_validate(
            {
                "class_id": str(wanted),
                "song_list_revision_id": str(revision),
                "swedebeat_connection_id": made.get("swedebeat_connection_id"),  # pyright: ignore[reportUnknownMemberType]
            }
        )
    except ValidationError:
        return _refuse("invalid_input")
    done = await job_enqueue()(
        factory,
        ctx,
        parsed,
        STUDIO_DESIGN,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        consumes_credit=STUDIO_DESIGN.consumes_credit,
    )
    if done.job_id is None or done.state is None:
        return _refuse(done.reason or "invalid_input", dict(done.counts))
    out = StudioJobOut(job_id=done.job_id, kind=KIND, state=done.state)
    if not done.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out
