"""Recent authentication, plus step-up when TOTP is confirmed (ADR 0003 section 6).

``require_sensitive`` is ``require_recent_auth`` and, for a user whose TOTP is confirmed, also a
TOTP or recovery code within 5 minutes (``require_stepup``'s check). It covers the password and
email changes and the deletion of the account. #39 owns enrolment and the step-up codes; this
only reads whether a confirmed TOTP exists.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from classbeat.auth.deps import current_session, require_recent_auth
from classbeat.auth.sessions import SessionInfo
from classbeat.auth.tables import mfa_totp
from classbeat.context import UserContext
from classbeat.db import system_session


async def require_sensitive(
    request: Request,
    ctx: Annotated[UserContext, Depends(require_recent_auth)],
    info: Annotated[SessionInfo | None, Depends(current_session)],
) -> UserContext:
    async with system_session(request.app.state.session_factory) as db:
        confirmed = (
            await db.execute(
                select(mfa_totp.c.id).where(
                    mfa_totp.c.user_id == ctx.user_id, mfa_totp.c.confirmed_at.is_not(None)
                )
            )
        ).first()
    if confirmed is not None and (info is None or not info.recent_stepup):
        raise HTTPException(status_code=403, detail={"reason": "stepup_required"})
    return ctx
