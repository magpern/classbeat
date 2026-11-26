"""``POST /api/classes/{id}/reread`` (#1536; ``classbeat.classes.reread``).

Body ``{interpret_job_id, answers: [{question_index, option_index | text}], note?}``: 201 with the
new interpret job ``{job_id, kind, state}``, 200 with the same job for a repeated or concurrent
call; 422
``invalid_input`` for a body that is not that and 422 ``invalid_answer`` or ``too_large`` for
answers that name no question or option or a request over its limit; 409 ``{reason}`` (``not_ready``
while the reading is not stored or ``interpret_job_id`` names another one, ``chat_full``,
``class_designed`` once the class has a revision or a design job, ``class_busy`` while a job of
the class is open, ``not_connected``,
the jobs API's own refusals); 404 ``not_found`` for another user's class like an unknown one. No
``Idempotency-Key`` header: the key is derived in ``classbeat.classes.reread``.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from classbeat import auth
from classbeat.classes import reread
from classbeat.classes.handoff_routes import job_enqueue
from classbeat.classes.interpretation_routes import DesignJobOut, _refuse  # pyright: ignore[reportPrivateUsage]
from classbeat.context import UserContext
from classbeat.design.interpret_reread_policy import RereadIn
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy

router = APIRouter(prefix="/api/classes", tags=["classes"])

_UNPROCESSABLE = frozenset({"invalid_input", "invalid_answer", "too_large"})


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


@router.post(
    "/{class_id}/reread",
    status_code=201,
    response_model=DesignJobOut,
    responses={200: {"model": DesignJobOut}, 404: {}, 409: {}, 422: {}},
)
async def reread_route(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
) -> DesignJobOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    try:
        body = RereadIn.model_validate_json(await request.body() or b"{}")
    except ValidationError:
        return JSONResponse({"detail": {"reason": "invalid_input"}}, status_code=422)
    done = await reread.reread(
        request.app.state.session_factory,
        ctx,
        wanted,
        body,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    if done.job_id is None or done.kind is None or done.state is None:
        reason = done.reason or "invalid_input"
        if reason in _UNPROCESSABLE:
            return JSONResponse({"detail": {"reason": reason}}, status_code=422)
        return _refuse(reason, dict(done.counts))
    out = DesignJobOut(job_id=done.job_id, kind=done.kind, state=done.state)
    if not done.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out
