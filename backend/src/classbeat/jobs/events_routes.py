"""``GET /api/jobs/{id}/events``: the job's progress as server-sent events (ADR 0005 section 6).

Signed in only (401). A job that is not the caller's, an unknown one and a malformed id are the
same 404. ``Last-Event-ID`` (or ``?after=`` for the first connect) names the last ``seq`` the
browser has. A job that has ended and has no event after that id answers 204 (#116 item 4): an
``EventSource`` stops reconnecting on it, and nothing is left to send. At most
``MAX_STREAMS_PER_USER`` open streams per user and web process (429 beyond). The stream itself
is ``classbeat.jobs.stream``. Each 15 s check of the session goes through ``resolve_session``,
which refreshes ``last_seen_at`` (throttled), so an open stream keeps the session alive.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.jobs import stream
from classbeat.jobs.progress import read_page

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

MAX_STREAMS_PER_USER: Final = 5
SEQ_MAX: Final = 2**63 - 1
RETRY_AFTER_SECONDS: Final = 5


class StreamLimiter:
    """Open streams per user in this process. A slot expires a little after the longest stream,
    so one that was never released (a connection that died before its first frame) cannot lock
    a user out for good."""

    def __init__(self) -> None:
        self._slots: dict[UUID, dict[object, float]] = {}

    def acquire(self, user_id: UUID) -> object | None:
        now = time.monotonic()
        slots = {k: d for k, d in self._slots.get(user_id, {}).items() if d > now}
        self._slots[user_id] = slots
        if len(slots) >= MAX_STREAMS_PER_USER:
            return None
        token = object()
        slots[token] = now + stream.MAX_STREAM_SECONDS + 2 * stream.KEEPALIVE_SECONDS
        return token

    def release(self, user_id: UUID, token: object) -> None:
        slots = self._slots.get(user_id)
        if slots is not None:
            slots.pop(token, None)
            if not slots:
                del self._slots[user_id]


def session_probe(
    factory: async_sessionmaker[AsyncSession], settings: Settings, token: str | None, user_id: UUID
) -> Callable[[], Awaitable[bool]]:
    """Whether the session the stream was opened with is still valid for the same user."""

    async def still_signed_in() -> bool:
        resolution = await auth.resolve_session(factory, settings, token)
        return resolution.info is not None and resolution.info.user_id == user_id

    return still_signed_in


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"reason": "not_found"})


def _parse_job_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise _not_found() from None


def _parse_after(request: Request, after: str | None) -> int:
    raw = request.headers.get("last-event-id")
    if raw is None or raw == "":
        raw = after
    if raw is None or raw == "":
        return 0
    if not raw.isascii() or not raw.isdigit() or int(raw) > SEQ_MAX:
        raise HTTPException(status_code=400, detail={"reason": "bad_last_event_id"})
    return int(raw)


@router.get("/{job_id}/events", response_model=None)
async def job_events(
    job_id: str,
    request: Request,
    ctx: Annotated[UserContext, Depends(auth.require_user)],
    after: str | None = None,
) -> StreamingResponse | JSONResponse | Response:
    uid = _parse_job_id(job_id)
    start = _parse_after(request, after)
    app = request.app
    factory = app.state.session_factory
    first = await read_page(factory, ctx, uid, start, 1)
    if first is None:
        raise _not_found()  # before the limit, so a foreign id never meets a 429
    if first.state in stream.TERMINAL_STATES and not first.events:
        return Response(status_code=204)  # over, and the browser has every event
    limiter: StreamLimiter = app.state.stream_limiter
    slot = limiter.acquire(ctx.user_id)
    if slot is None:
        return JSONResponse(
            {"detail": {"reason": "too_many_streams"}},
            status_code=429,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    token = auth.request_session_token(request)
    settings = app.state.settings

    async def frames() -> AsyncIterator[str]:
        try:
            async for chunk in stream.event_stream(
                factory,
                app.state.event_hub,
                ctx,
                uid,
                start,
                still_signed_in=session_probe(factory, settings, token, ctx.user_id),
            ):
                yield chunk
        finally:
            limiter.release(ctx.user_id, slot)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
