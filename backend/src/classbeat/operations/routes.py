"""``/api/external-operations``: what the user sees of their writes, and their two decisions on
an uncertain one (ADR 0005 section 7).

- ``GET /api/external-operations[?job_id=]``: the user's ``uncertain`` operations (of one job).
- ``GET /api/external-operations/{id}``: one operation; 404 for another user's or an unknown id.
- ``POST /api/external-operations/{id}/check-again``: reconcile once more.
- ``POST /api/external-operations/{id}/do-it-anyway``: the user decides it did not happen.

Signed in; the ``POST``s are CSRF-checked by the middleware. Recent authentication is not
needed (the user's own write, like approving a list). A refusal is 409 with a reason:
``final`` (a ``succeeded`` row) or ``not_uncertain``. No provider id, key or result is shown.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from classbeat import auth
from classbeat.context import UserContext
from classbeat.operations import user
from classbeat.operations.user import OperationView, Resolution

router = APIRouter(prefix="/api/external-operations", tags=["external-operations"])


class OperationOut(BaseModel):
    id: UUID
    kind: str
    state: str
    job_id: UUID | None
    resolved_by: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None

    @classmethod
    def of(cls, view: OperationView) -> "OperationOut":
        return cls(
            id=view.id,
            kind=view.kind,
            state=view.state,
            job_id=view.job_id,
            resolved_by=view.resolved_by,
            created_at=view.created_at,
            updated_at=view.updated_at,
            resolved_at=view.resolved_at,
        )


class OperationsOut(BaseModel):
    operations: list[OperationOut]


def _not_found() -> JSONResponse:
    return JSONResponse({"detail": {"reason": "not_found"}}, status_code=404)


def _answer(resolution: Resolution) -> OperationOut | JSONResponse:
    if resolution.operation is None or resolution.reason == "not_found":
        return _not_found()
    if resolution.reason is not None:
        return JSONResponse({"detail": {"reason": resolution.reason}}, status_code=409)
    return OperationOut.of(resolution.operation)


@router.get("")
async def list_uncertain(
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    job_id: UUID | None = None,
) -> OperationsOut:
    views = await user.uncertain(request.app.state.session_factory, ctx, job_id)
    return OperationsOut(operations=[OperationOut.of(v) for v in views])


@router.get("/{operation_id}", response_model=OperationOut, responses={404: {}})
async def get_operation(
    operation_id: UUID,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> OperationOut | JSONResponse:
    view = await user.get(request.app.state.session_factory, ctx, operation_id)
    return _not_found() if view is None else OperationOut.of(view)


@router.post(
    "/{operation_id}/check-again", response_model=OperationOut, responses={404: {}, 409: {}}
)
async def check_again(
    operation_id: UUID,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> OperationOut | JSONResponse:
    state = request.app.state
    return _answer(await user.check_again(state.session_factory, state.keyring, ctx, operation_id))


@router.post(
    "/{operation_id}/do-it-anyway", response_model=OperationOut, responses={404: {}, 409: {}}
)
async def do_it_anyway(
    operation_id: UUID,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> OperationOut | JSONResponse:
    return _answer(await user.do_it_anyway(request.app.state.session_factory, ctx, operation_id))
