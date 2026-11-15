"""``/api/classes/{class_id}/studio-songs/{revision_id}``: a studio song list and the user's actions
on it (ADR 0036 section 3.3; ``classbeat.classes.studio_songs``).

- ``GET``: the list as the page shows it (no Spotify id, no catalogue reference).
- ``POST .../undo`` and ``POST .../another`` with ``{"position": n}``, ``POST .../swap-anyway``
  with ``{}``: 201 with the new ``studio_songs`` job ``{job_id, kind, state}``, 200 with the same
  job for a repeated ``Idempotency-Key``; 409 ``{reason, ...counts}`` with the refusal (the
  waiting list is left as it was); 404 ``not_found`` (also with the studio off: ``feature_off``).

Signed in (``auth.require_user``) and, like every unsafe request, with the CSRF token the auth
middleware checks. Another user's class or list is 404 like an unknown one.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from classbeat import auth
from classbeat.agent.schemas import MAX_SONGS
from classbeat.classes import studio_songs
from classbeat.classes.handoff_routes import job_enqueue
from classbeat.context import UserContext
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import IDEMPOTENCY_KEY, credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy

router = APIRouter(prefix="/api/classes", tags=["classes"])

_STATUS = {
    "not_found": 404,
    "feature_off": 404,
    "invalid_input": 422,
    "too_large": 422,
    "busy": 429,
    "idempotency_key_missing": 400,
    "idempotency_key_invalid": 400,
}


class PositionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int | None = Field(default=None, ge=0, lt=MAX_SONGS)


class StudioJobOut(BaseModel):
    job_id: UUID
    kind: str
    state: str


def _refuse(reason: str, counts: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"reason": reason, **(counts or {})}
    if reason == "feature_off":
        body = {"reason": "not_found"}  # the studio off answers like an unknown path
    return JSONResponse({"detail": body}, status_code=_STATUS.get(reason, 409))


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


@router.get("/{class_id}/studio-songs/{revision_id}", responses={404: {}})
async def get_studio_songs(
    class_id: str,
    revision_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
) -> JSONResponse:
    wanted, revision = _uuid(class_id), _uuid(revision_id)
    if wanted is None or revision is None:
        return _refuse("not_found")
    found = await studio_songs.view(request.app.state.session_factory, ctx, wanted, revision)
    if isinstance(found, str):
        return _refuse(found)
    return JSONResponse(jsonable(found))


def jsonable(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "revision_id": str(value["revision_id"])}


_ACT = {
    "status_code": 201,
    "response_model": StudioJobOut,
    "responses": {200: {"model": StudioJobOut}, 400: {}, 404: {}, 409: {}, 422: {}, 429: {}},
}
Ledger = Annotated[CreditLedger, Depends(credit_ledger)]
Limit = Annotated[JobLimitPolicy, Depends(job_limit)]
Key = Annotated[str | None, Header()]


@router.post("/{class_id}/studio-songs/{revision_id}/undo", **_ACT)
async def undo_studio_song(
    class_id: str, revision_id: str, request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)], ledger: Ledger, limit: Limit,
    idempotency_key: Key = None,
) -> StudioJobOut | JSONResponse:  # fmt: skip
    return await _act("undo", class_id, revision_id, request, ctx, ledger, limit, idempotency_key)


@router.post("/{class_id}/studio-songs/{revision_id}/another", **_ACT)
async def another_studio_song(
    class_id: str, revision_id: str, request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)], ledger: Ledger, limit: Limit,
    idempotency_key: Key = None,
) -> StudioJobOut | JSONResponse:  # fmt: skip
    return await _act(
        "another", class_id, revision_id, request, ctx, ledger, limit, idempotency_key
    )


@router.post("/{class_id}/studio-songs/{revision_id}/swap-anyway", **_ACT)
async def swap_studio_songs_anyway(
    class_id: str, revision_id: str, request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)], ledger: Ledger, limit: Limit,
    idempotency_key: Key = None,
) -> StudioJobOut | JSONResponse:  # fmt: skip
    return await _act(
        "swap_anyway", class_id, revision_id, request, ctx, ledger, limit, idempotency_key
    )


async def _act(
    chosen: Literal["undo", "another", "swap_anyway"],
    class_id: str,
    revision_id: str,
    request: Request,
    ctx: UserContext,
    ledger: CreditLedger,
    limit: JobLimitPolicy,
    idempotency_key: str | None,
) -> StudioJobOut | JSONResponse:
    wanted, revision = _uuid(class_id), _uuid(revision_id)
    if wanted is None or revision is None:
        return _refuse("not_found")
    if idempotency_key is None:
        return _refuse("idempotency_key_missing")
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return _refuse("idempotency_key_invalid")
    try:
        raw = await request.body()
        body = PositionIn.model_validate_json(raw or b"{}")
    except ValidationError:
        return _refuse("invalid_input")
    done = await studio_songs.act(
        request.app.state.session_factory,
        ctx,
        wanted,
        revision,
        chosen,
        body.position,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    if done.job_id is None or done.state is None:
        return _refuse(done.reason or "invalid_input", dict(done.counts))
    out = StudioJobOut(job_id=done.job_id, kind=studio_songs.KIND, state=done.state)
    if not done.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out
