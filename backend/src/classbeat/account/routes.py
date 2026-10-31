"""``/api/account/export`` and ``/api/account/delete`` (ADR 0014; M6, #337).

Both need recent authentication and, once the user's TOTP is confirmed, a step-up
(``auth.require_recent_auth``, ADR 0003 section 6); the POST passes the CSRF and origin checks
of ``classbeat.auth``.

- ``GET /api/account/export``: the user's own data as one JSON download. 5 per user and 20 per IP
  and hour, every request counted.
- ``POST /api/account/delete {confirm_email}``: 400 ``confirmation_mismatch`` unless the typed
  address is the account's; otherwise open jobs end, credits settle, the account goes (204) and
  the session cookie is cleared.
"""

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from classbeat import auth, quota
from classbeat.account import erase
from classbeat.account.export import build_export
from classbeat.context import UserContext

router = APIRouter(prefix="/api/account", tags=["account"])


class DeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_email: str = Field(max_length=320)


def _refusal(reason: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": {"reason": reason}}, status_code=status)


def ledger(request: Request) -> quota.MeteredLedger:
    return quota.MeteredLedger(quota.Bounds.from_settings(request.app.state.settings))


@router.get("/export", responses={403: {}, 404: {}, 429: {}})
async def get_export(
    request: Request, ctx: Annotated[UserContext, Depends(auth.require_recent_auth)]
) -> Response:
    attempt = await request.app.state.limiter.begin(
        auth.export_buckets(str(ctx.user_id), auth.request_ip(request))
    )
    if not attempt.allowed:
        raise auth.RateLimited(attempt.retry_after)
    now = datetime.now(UTC)
    document = await build_export(request.app.state.session_factory, ctx, now=now)
    if document is None:
        return _refusal("not_found", 404)
    return Response(
        json.dumps(document, indent=2, ensure_ascii=False).encode(),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="classbeat-export-{now:%Y-%m-%d}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/delete", status_code=204, responses={400: {}, 403: {}, 404: {}, 422: {}})
async def post_delete(
    request: Request,
    body: DeleteIn,
    ctx: Annotated[UserContext, Depends(auth.require_recent_auth)],
) -> Response:
    factory = request.app.state.session_factory
    if not await erase.confirmed(factory, ctx, body.confirm_email):
        return _refusal("confirmation_mismatch", 400)
    await erase.end_jobs(factory, ctx, ledger(request))
    if not await erase.delete_row(factory, ctx):
        return _refusal("not_found", 404)
    out = Response(status_code=204)
    auth.clear_session_cookie(out)
    return out
