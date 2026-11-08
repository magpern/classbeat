"""The passkey sign-in finish's one write transaction (ADR 0019 sections 5, 6 and 7, #773).

``complete`` runs after the assertion was verified with no lock held. In one transaction, in this
order:

1. ``lock_user`` on the credential's user, the first lock (section 6, rule 2).
2. The counter rule (``passkey_counter.advance_or_refuse``). No row back: if the credential is
   gone (removed, or a reset that committed first deleted it) nothing is written; if it still
   exists the counter regressed, so ``passkey_counter_regressed`` is recorded, the user is mailed
   at most once a day per passkey, the transaction commits with no session and the passkey stays.
3. A disabled user gets the generic failure, like a wrong password.
4. The ``ADMIN_EMAILS`` grant and ``start_session`` as for a password sign-in (#632's client mark,
   ``reauth_at`` now, never ``mfa_at``: an admin still needs TOTP in the new session), the known
   device and the ``sign_in`` event ``{method: "passkey"}``. A ``cross_platform`` assertion
   (owner question 4, #776) caps the new session at 24 hours (``session_cap``) and makes no
   known device, so ``device`` stays empty and the route sets no device cookie; its ``sign_in``
   event says ``cross_device: true``.

So a reset either commits first (the credential is gone, or the session is never made) or waits
for this commit and then revokes the new session with the rest.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.accounts import grant_admin_if_listed
from classbeat.auth.deps import request_client_mark, request_device_token, request_session_token
from classbeat.auth.devices import remember_device
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import factory, user_agent
from classbeat.auth.passkey_assertion import Asserted
from classbeat.auth.passkey_attachment_policy import CROSS_DEVICE_SESSION
from classbeat.auth.passkey_counter import METHOD, advance_or_refuse, record_passkey_failure
from classbeat.auth.passkey_signin_store import SignInCredential
from classbeat.auth.session_cap import cap_session
from classbeat.auth.sessions import IssuedSession, start_session
from classbeat.auth.tables import users
from classbeat.auth.userlock import lock_user
from classbeat.db import system_session
from classbeat.emails.notices import Notice


@dataclass(frozen=True, slots=True)
class SignedInUser:
    id: UUID
    email: str
    role: str


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the transaction decided: a session (``issued``, ``device``, ``user``) or a failure
    category in ``reason``; ``notice`` is the counter mail to send, if any. ``device`` is empty
    when no known device was made (a cross-platform sign-in)."""

    reason: str | None = None
    issued: IssuedSession | None = None
    device: str = ""
    user: SignedInUser | None = None
    notice: Notice | None = None


async def record_failure(
    db: AsyncSession, user_id: UUID | None, ip: str, reason: str, passkey: str | None = None
) -> None:
    """``sign_in_failed`` with the category only (section 10): never a label, id or key."""
    await record_passkey_failure(db, "sign_in_failed", user_id, ip, reason, passkey)


async def complete(
    request: Request,
    found: SignInCredential,
    asserted: Asserted,
    ip: str,
    *,
    cross_platform: bool,
) -> Outcome:
    settings, user_id, passkey = request.app.state.settings, found.user_id, str(found.id)
    async with system_session(factory(request)) as db:
        locked = await lock_user(db, user_id)
        if locked is None:
            return Outcome(reason="gone")  # the account was deleted meanwhile; nothing written
        counted = await advance_or_refuse(
            db,
            passkey_id=found.id,
            user_id=user_id,
            asserted=asserted,
            ip=ip,
            failed="sign_in_failed",
        )
        if counted.reason is not None:
            return Outcome(reason=counted.reason, notice=counted.notice)
        if locked.disabled:
            await record_failure(db, user_id, ip, "disabled", passkey)
            return Outcome(reason="disabled")  # the same answer as any other failure
        await grant_admin_if_listed(db, settings, user_id)  # may revoke every session
        issued = await start_session(
            db,
            settings,
            user_id,
            user_agent=user_agent(request),
            ip=ip,
            replacing_token=request_session_token(request),
            mark=request_client_mark(request, ip),
        )
        device = ""
        detail: dict[str, object] = {"method": METHOD, "passkey": passkey}
        if cross_platform:  # client-reported: only ever makes the session stricter
            issued = await cap_session(db, issued, CROSS_DEVICE_SESSION)
            detail["cross_device"] = True  # for support and audit: this session was capped
        else:
            device = await remember_device(
                db, request.app.state.keys, user_id, request_device_token(request)
            )
        await record_event(db, "sign_in", user_id=user_id, ip=ip, detail=detail)
        row = (
            await db.execute(select(users.c.email, users.c.role).where(users.c.id == user_id))
        ).one()
    return Outcome(issued=issued, device=device, user=SignedInUser(user_id, row[0], row[1]))
