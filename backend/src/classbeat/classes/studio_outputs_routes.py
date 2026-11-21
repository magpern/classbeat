"""A studio class's timeline and output choice (ADR 0036 sections 5 and 6, SR C6a).

- ``GET /api/classes/{class_id}/timeline``: the current studio draft's songs, cuts, crossfades,
  effective lengths and intervals, from the composed map (``classes.studio_timeline``). 404
  ``not_found`` for another user's or an unknown class and with the studio off; 409 ``{reason}``
  for ``no_studio_draft`` and ``music_changed``.
- ``POST /api/classes/{class_id}/outputs`` with ``{revision_id, content_hash, outputs}`` and an
  ``Idempotency-Key``: the user's approval of that draft for those outputs
  (``classes.studio_outputs``): 201 with the ``produce_outputs`` job ``{job_id, kind, state}``,
  200 with the same job for a repeated key and body; 409 ``{reason}`` with the refusal
  (``hash_mismatch``, ``not_current``, ``mowl_needs_mix``, ``playlist_has_repeats``, ``output_off``,
  ``connection_changed``, ``session_refused``, ``class_busy``, ``idempotency_mismatch`` ...), 404
  ``not_found`` as above.

Signed in (``auth.require_user``) and, like every unsafe request, with the CSRF token the auth
middleware checks.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from classbeat import auth
from classbeat.classes import studio_outputs, studio_timeline
from classbeat.classes.handoff_routes import job_enqueue
from classbeat.classes.studio_songs_routes import StudioJobOut, _uuid  # pyright: ignore[reportPrivateUsage]
from classbeat.context import UserContext
from classbeat.design.revise import Sha256Hex
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import IDEMPOTENCY_KEY, credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy
from classbeat.studio.output_choice_policy import OUTPUTS
from classbeat.swedebeat_studio.outputs_admit import KIND

router = APIRouter(prefix="/api/classes", tags=["classes"])

_STATUS = {
    "not_found": 404,
    "class_not_found": 404,
    "feature_off": 404,
    "invalid_input": 422,
    "too_large": 422,
    "busy": 429,
    "rate_limited": 429,
    "idempotency_key_missing": 400,
    "idempotency_key_invalid": 400,
    "app_misconfigured": 503,
    "key_unavailable": 503,
    "upstream_unavailable": 503,
    "invalid_response": 502,
    "outbound_blocked": 502,
}


class OutputsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str = Field(max_length=64)
    content_hash: Sha256Hex
    outputs: list[str] = Field(min_length=1, max_length=len(OUTPUTS))


def _refuse(reason: str, counts: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"reason": reason, **(counts or {})}
    if reason in ("feature_off", "class_not_found"):
        body = {"reason": "not_found"}  # answered like an unknown path or class
    return JSONResponse({"detail": body}, status_code=_STATUS.get(reason, 409))


@router.get("/{class_id}/timeline", responses={404: {}, 409: {}})
async def get_timeline(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    found = await studio_timeline.view(request.app.state.session_factory, ctx, wanted)
    if isinstance(found, str):
        return _refuse(found)
    return JSONResponse(found)


@router.post(
    "/{class_id}/outputs",
    status_code=201,
    response_model=StudioJobOut,
    responses={
        200: {"model": StudioJobOut}, 400: {}, 404: {}, 409: {}, 422: {}, 429: {}, 502: {}, 503: {}
    },
)  # fmt: skip
async def choose_outputs(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> StudioJobOut | JSONResponse:
    wanted = _uuid(class_id)
    if wanted is None:
        return _refuse("not_found")
    if idempotency_key is None:
        return _refuse("idempotency_key_missing")
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return _refuse("idempotency_key_invalid")
    try:
        body = OutputsIn.model_validate_json(await request.body())
    except ValidationError:
        return _refuse("invalid_input")
    revision = _uuid(body.revision_id)
    if revision is None:
        return _refuse("invalid_input")
    done = await studio_outputs.choose(
        request.app.state.session_factory,
        request.app.state.keyring,
        ctx,
        wanted,
        revision,
        body.content_hash,
        body.outputs,
        idempotency_key=idempotency_key,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    if done.job_id is None or done.state is None:
        return _refuse(done.reason or "invalid_input", dict(done.counts))
    out = StudioJobOut(job_id=done.job_id, kind=KIND, state=done.state)
    if not done.created:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    return out
