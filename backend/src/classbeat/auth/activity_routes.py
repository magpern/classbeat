"""``GET /api/auth/events``: the caller's own security activity (#678, ``activity``).

Moved out of ``auth.routes`` (#1294, split on touch); the route and its answer are unchanged.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from classbeat.auth.activity import (
    CURSOR_PATTERN,
    PAGE_DEFAULT,
    PAGE_MAX,
    decode_cursor,
    list_activity,
)
from classbeat.auth.activity_schema import ActivityItemOut, ActivityOut
from classbeat.auth.deps import require_user
from classbeat.context import UserContext
from classbeat.db import system_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/events")
async def list_own_events(
    request: Request,
    ctx: Annotated[UserContext, Depends(require_user)],
    limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = PAGE_DEFAULT,
    before: Annotated[str | None, Query(pattern=CURSOR_PATTERN, max_length=60)] = None,
) -> ActivityOut:
    """The caller's own security activity, newest first (#678; ``activity``). ``before`` is the
    ``next_before`` of the previous page; a cursor that names no time we could make is a 422."""
    position = None if before is None else decode_cursor(before)
    if before is not None and position is None:
        raise HTTPException(status_code=422)
    async with system_session(request.app.state.session_factory) as db:
        page = await list_activity(db, ctx.user_id, limit=limit, before=position)
    return ActivityOut(
        events=[
            ActivityItemOut(
                id=item.id,
                kind=item.kind,
                at=item.at,
                ip=item.ip,
                provider=item.provider,
                other_session=item.other_session,
                passkey=item.passkey,
            )
            for item in page.items
        ],
        next_before=page.next_cursor,
    )
