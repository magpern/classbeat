"""``/api/classes/{id}/pending-messages`` (ADR 0038 section 8; ``classbeat.classes.pending``).

Only while ``composer_flow`` is on: 409 ``feature_off`` otherwise.

- ``GET .../pending-messages``: the class's stored messages, oldest first.
- ``POST .../pending-messages`` ``{message_id, body}``: 201 with the stored ``pending`` message,
  200 with the same one for the same id and body; 409 ``idempotency_mismatch`` for the same id with
  another body, 409 ``message_pending`` while another message of the class is pending, 409
  ``pending_full`` (``limit``, ``count``) once the class holds ``pending.MAX_STORED`` messages.
- ``POST .../pending-messages/{message_id}/submit``: 201 when it became a job now, 200 with the
  message as it is (``submitted`` with its ``job_id``, or ``failed`` with its ``reason``) for a
  message submitted or refused earlier; 409 ``class_busy`` while a job of the class is queued or
  running (the message stays pending); 409 ``{reason, ...counts}`` with the refusal it was
  ``failed`` with now.

404 ``not_found`` for no such class or message of the user's. Errors carry a code and whole
numbers only.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from classbeat import auth
from classbeat.classes import pending, pending_submit
from classbeat.classes.handoff_routes import job_enqueue
from classbeat.context import UserContext
from classbeat.design.revise import Request as MessageBody
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy

router = APIRouter(prefix="/api/classes", tags=["classes"])


class PendingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    body: MessageBody


class PendingOut(BaseModel):
    message_id: UUID
    body: str
    status: str
    reason: str | None
    job_id: UUID | None
    created_at: datetime

    @classmethod
    def of(cls, message: pending.Message) -> "PendingOut":
        return cls(
            message_id=message.message_id,
            body=message.body,
            status=message.status,
            reason=message.reason,
            job_id=message.job_id,
            created_at=message.created_at,
        )


class PendingListOut(BaseModel):
    messages: list[PendingOut]


def _refuse(reason: str, counts: Any = None) -> JSONResponse:
    status = 404 if reason == "not_found" else 409
    return JSONResponse({"detail": {"reason": reason, **(counts or {})}}, status_code=status)


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _answer(done: pending.Answer) -> PendingOut | JSONResponse:
    if done.reason is not None or done.message is None:
        return _refuse(done.reason or "internal", dict(done.counts))
    out = PendingOut.of(done.message)
    if not done.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out


@router.get(
    "/{class_id}/pending-messages", response_model=PendingListOut, responses={404: {}, 409: {}}
)
async def list_pending(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> PendingListOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    found = await pending.listed(request.app.state.session_factory, ctx, wanted)
    if isinstance(found, str):
        return _refuse(found)
    return PendingListOut(messages=[PendingOut.of(m) for m in found])


@router.post(
    "/{class_id}/pending-messages",
    status_code=201,
    response_model=PendingOut,
    responses={200: {"model": PendingOut}, 404: {}, 409: {}, 422: {}},
)
async def store_pending(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> PendingOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    try:
        body = PendingIn.model_validate_json(await request.body())
    except ValidationError:
        return JSONResponse({"detail": {"reason": "invalid_input"}}, status_code=422)
    factory = request.app.state.session_factory
    return _answer(await pending.store(factory, ctx, wanted, body.message_id, body.body))


@router.post(
    "/{class_id}/pending-messages/{message_id}/submit",
    status_code=201,
    response_model=PendingOut,
    responses={200: {"model": PendingOut}, 404: {}, 409: {}},
)
async def submit_pending(
    class_id: str,
    message_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
) -> PendingOut | JSONResponse:
    wanted, message = _uuid(class_id), _uuid(message_id)
    if wanted is None or message is None:
        return _refuse("not_found")
    done = await pending_submit.submit(
        request.app.state.session_factory,
        ctx,
        wanted,
        message,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    return _answer(done)
