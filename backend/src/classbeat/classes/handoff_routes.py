"""``/api/classes/{id}/swedebeat-handoff``: the manual Swedebeat step (ADR 0016 section 7).

The flag is answered before the body is read and before the class is. A request carries a class
id and, on a check or an override, at most a mix id. ``state``, the match revision and the
expiry are the server's. An expired row is 410 ``handoff_expired`` and is never revived.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from classbeat import auth
from classbeat.classes import handoff
from classbeat.connections.swedebeat_account import swedebeat_enabled
from classbeat.context import UserContext
from classbeat.jobs.credits import CreditLedger
from classbeat.jobs.http import IDEMPOTENCY_KEY, JobEnqueue, credit_ledger, job_limit
from classbeat.jobs.user import JobLimitPolicy
from classbeat.swedebeat_handoff.rows import Handoff

router = APIRouter(prefix="/api/classes", tags=["classes"])

_STATUS = {
    "not_found": 404,
    "stale_match": 409,
    "handoff_expired": 410,
    "class_busy": 409,
    "not_connected": 409,
    "needs_reconnect": 409,
    "decrypt_failed": 409,
    "rate_limited": 429,
    "upstream_unavailable": 503,
    "key_unavailable": 503,
    "invalid_input": 422,
    "not_checked": 409,
    "outbound_blocked": 502,
    "busy": 429,
    "quota": 409,
    "analysis_limit": 409,
    "too_large": 422,
    "idempotency_mismatch": 409,
    "feature_off": 404,
    "idempotency_key_missing": 400,
    "idempotency_key_invalid": 400,
}


class MixIdIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mix_id: str | None = Field(default=None, pattern=r"^[0-9A-Za-z_-]{1,64}$")


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mix_id: str = Field(pattern=r"^[0-9A-Za-z_-]{1,64}$")


class HandoffOut(BaseModel):
    state: Literal["open", "checked", "verified"]
    verdict: Literal["ok", "too_short", "too_long", "not_found"] | None = None
    mix_id: str | None = None
    candidates: list[str]
    missing_positions: list[int]
    analysed_sec: float | None = None
    expected_low_sec: int | None = None
    expected_high_sec: int | None = None
    expires_at: datetime
    checked_at: datetime | None = None


class CheckOut(BaseModel):
    job_id: UUID | None = None
    handoff: HandoffOut | None = None


class DesignOut(BaseModel):
    job_id: UUID | None = None
    revision_id: UUID | None = None
    designed: bool = False


class DismissedOut(BaseModel):
    dismissed: Literal[True] = True
    handoff: HandoffOut | None = None


def _not_found() -> JSONResponse:
    return _refuse("not_found")


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _refuse(reason: str, counts: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"reason": reason}
    if counts:
        body.update(counts)
    headers = {}
    if reason == "rate_limited":
        retry = counts.get("retry_after") if counts else None
        headers["Retry-After"] = str(retry if isinstance(retry, int) else 60)
    return JSONResponse({"detail": body}, status_code=_STATUS.get(reason, 409), headers=headers)


def _numbers(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int)]


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _out(row: Handoff) -> HandoffOut:
    detail = row.detail
    low = detail.get("expected_low_sec")
    high = detail.get("expected_high_sec")
    verdict = row.verdict if row.verdict in ("ok", "too_short", "too_long", "not_found") else None
    state = row.state if row.state in ("open", "checked", "verified") else "open"
    return HandoffOut(
        state=state,  # pyright: ignore[reportArgumentType]
        verdict=verdict,  # pyright: ignore[reportArgumentType]
        mix_id=row.mix_id,
        candidates=_strings(detail.get("candidates")),
        missing_positions=_numbers(detail.get("missing_positions")),
        analysed_sec=_seconds(detail.get("analysed_sec")),
        expected_low_sec=low if isinstance(low, int) else None,
        expected_high_sec=high if isinstance(high, int) else None,
        expires_at=row.expires_at,
        checked_at=row.checked_at,
    )


def _step(done: handoff.Step) -> JSONResponse | None:
    if done.reason is not None:
        return _refuse(done.reason, dict(done.counts))
    return None


async def _enabled(request: Request) -> bool:
    return await swedebeat_enabled(request.app.state.settings, request.app.state.session_factory)


_job_enqueue: JobEnqueue | None = None


def install_job_enqueue(fn: JobEnqueue) -> None:
    """The web app installs ``classbeat.jobs.routes.enqueue``.

    This module does not import ``classbeat.jobs.routes`` or ``classbeat.jobs.service``.
    The session guard allows the service only from the jobs routes, and those only from the
    web app.
    """
    global _job_enqueue
    _job_enqueue = fn


def job_enqueue() -> JobEnqueue:
    if _job_enqueue is None:
        raise RuntimeError("job creation is not installed")
    return _job_enqueue


def _key(header: str | None) -> JSONResponse | str:
    if header is None:
        return _refuse("idempotency_key_missing", {})
    if not IDEMPOTENCY_KEY.fullmatch(header):
        return _refuse("idempotency_key_invalid", {})
    return header


@router.get(
    "/{class_id}/swedebeat-handoff",
    response_model=HandoffOut,
    responses={404: {}, 409: {}, 410: {}},
)
async def get_handoff(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> HandoffOut | JSONResponse:
    if not await _enabled(request):
        return _refuse("feature_off")
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await handoff.read(request.app.state.session_factory, ctx, wanted)
    refused = _step(done)
    if refused is not None or done.handoff is None:
        return refused or _not_found()
    return _out(done.handoff)


@router.post(
    "/{class_id}/swedebeat-handoff",
    response_model=HandoffOut,
    responses={404: {}, 409: {}, 410: {}, 429: {}, 502: {}, 503: {}},
)
async def start_handoff(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> HandoffOut | JSONResponse:
    """Snapshot the user's mixes. The same live row again does not list them a second time."""
    if not await _enabled(request):
        return _refuse("feature_off")
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    factory = request.app.state.session_factory
    begun = await handoff.begin(factory, ctx, wanted)
    refused = _step(begun)
    if refused is not None:
        return refused
    if begun.handoff is not None:
        return _out(begun.handoff)
    attempt = await request.app.state.limiter.begin(
        auth.swedebeat_mixes_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    ids, reason = await handoff.list_mix_ids(factory, request.app.state.keyring, ctx)
    if reason is not None or ids is None:
        return _refuse(reason or "upstream_unavailable")
    opened = await handoff.open_from(factory, ctx, wanted, ids)
    refused = _step(opened)
    if refused is not None or opened.handoff is None:
        return refused or _not_found()
    return _out(opened.handoff)


@router.post(
    "/{class_id}/swedebeat-handoff/check",
    response_model=CheckOut,
    responses={400: {}, 404: {}, 409: {}, 410: {}, 422: {}, 429: {}},
)
async def check_handoff(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> CheckOut | JSONResponse:
    """ "I made the mix" and "Check again": one verify job. Verified is a 200 with no job."""
    if not await _enabled(request):
        return _refuse("feature_off")
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    key = _key(idempotency_key)
    if isinstance(key, JSONResponse):
        return key
    try:
        raw = await request.body()
        body = MixIdIn.model_validate_json(raw or b"{}")
    except ValidationError:
        return _refuse("invalid_input")
    done = await handoff.press(
        request.app.state.session_factory,
        ctx,
        wanted,
        body.mix_id,
        idempotency_key=key,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    refused = _step(done)
    if refused is not None:
        return refused
    status = 201 if done.created else 200
    payload = CheckOut(
        job_id=done.job_id, handoff=None if done.handoff is None else _out(done.handoff)
    )
    if status == 201:
        return JSONResponse(payload.model_dump(mode="json"), status_code=201)
    return payload


@router.post(
    "/{class_id}/swedebeat-handoff/override",
    response_model=HandoffOut,
    responses={404: {}, 409: {}, 410: {}, 422: {}, 502: {}, 503: {}},
)
async def override_handoff(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> HandoffOut | JSONResponse:
    """ "This is the right mix." The mix is the one the check analysed, still in the listing."""
    if not await _enabled(request):
        return _refuse("feature_off")
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    try:
        body = OverrideIn.model_validate_json(await request.body())
    except ValidationError:
        return _refuse("invalid_input")
    done = await handoff.override(
        request.app.state.session_factory, request.app.state.keyring, ctx, wanted, body.mix_id
    )
    refused = _step(done)
    if refused is not None or done.handoff is None:
        return refused or _not_found()
    return _out(done.handoff)


@router.post(
    "/{class_id}/swedebeat-handoff/design",
    response_model=DesignOut,
    responses={400: {}, 404: {}, 409: {}, 410: {}, 422: {}, 429: {}},
)
async def design_handoff(
    class_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    ledger: Annotated[CreditLedger, Depends(credit_ledger)],
    limit: Annotated[JobLimitPolicy, Depends(job_limit)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> DesignOut | JSONResponse:
    """Design the class on the verified mix (A4). Already designed: 200, no second job."""
    if not await _enabled(request):
        return _refuse("feature_off")
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    key = _key(idempotency_key)
    if isinstance(key, JSONResponse):
        return key
    done = await handoff.start_design(
        request.app.state.session_factory,
        ctx,
        wanted,
        idempotency_key=key,
        ledger=ledger,
        limit=limit,
        enqueue=job_enqueue(),
    )
    refused = _step(done)
    if refused is not None:
        return refused
    if done.revision_id is not None:
        return DesignOut(revision_id=done.revision_id, designed=True)
    status = 201 if done.created else 200
    payload = DesignOut(job_id=done.job_id)
    if status == 201:
        return JSONResponse(payload.model_dump(mode="json"), status_code=201)
    return payload


@router.post(
    "/{class_id}/swedebeat-handoff/not-now",
    response_model=DismissedOut,
    responses={404: {}, 410: {}},
)
async def dismiss_handoff(
    class_id: str, request: Request, ctx: Annotated[UserContext, Depends(auth.require_user)]
) -> DismissedOut | JSONResponse:
    """ "Not now." Writes nothing."""
    if not await _enabled(request):
        return _refuse("feature_off")
    wanted = _uuid(class_id)
    if wanted is None:
        return _not_found()
    done = await handoff.not_now(request.app.state.session_factory, ctx, wanted)
    refused = _step(done)
    if refused is not None:
        return refused
    return DismissedOut(handoff=None if done.handoff is None else _out(done.handoff))
