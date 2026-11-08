"""The passkey re-auth finish's lookup and its one write transaction (ADR 0019 sections 6, 7, 8
and 13, #774).

``find_own_credential`` looks the credential up with ``credential_id = :id AND user_id =
:session_user``, never by its id alone: another user's passkey, however valid its signature, is
not found, so the finish gives the generic failure and that passkey's counter and
``last_used_at`` are never touched (T120).

``complete`` runs after the assertion was verified with no lock held. In one transaction, in this
order:

1. ``lock_user`` on the session's user, the first lock (section 6, rule 2); a disabled user gets
   the generic failure before anything is touched.
2. The counter rule on that user's credential (``passkey_counter.advance_or_refuse``): a
   credential a reset deleted meanwhile is ``gone`` (nothing written); a regression is recorded,
   mailed at most once a day and commits with no rotation.
3. ``rotate_session`` with ``reauth=True`` and the request's client mark: ``reauth_at`` is now,
   ``stepup_at`` and ``mfa_at`` are carried over unchanged (a passkey is not a step-up, section
   8), and #632's ``location_changed_at`` is cleared as for a password re-auth (section 13). A
   session revoked meanwhile raises ``SessionGone`` out of the transaction, which rolls the
   counter update back with it.
4. The ``reauth`` event ``{method: "passkey", passkey: <row id>}``.

So a reset either commits first (the passkey or the session is gone, nothing rotated) or waits for
this commit and then revokes the rotated session with the rest.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.deps import request_client_mark
from classbeat.auth.events import record_event
from classbeat.auth.flowtools import factory, user_agent
from classbeat.auth.passkey_assertion import Asserted
from classbeat.auth.passkey_counter import METHOD, advance_or_refuse, record_passkey_failure
from classbeat.auth.sessions import IssuedSession, SessionInfo, rotate_session
from classbeat.auth.tables import webauthn_credentials
from classbeat.auth.userlock import lock_user
from classbeat.db import system_session
from classbeat.emails.notices import Notice


@dataclass(frozen=True, slots=True)
class OwnCredential:
    """One of the session user's own credentials, with what the finish checks before any lock."""

    id: UUID
    public_key: bytes
    backup_eligible: bool


@dataclass(frozen=True, slots=True)
class Outcome:
    """The rotated session (``issued``), or a failure category in ``reason``; ``notice`` is the
    counter mail to send, if any."""

    reason: str | None = None
    issued: IssuedSession | None = None
    notice: Notice | None = None


async def find_own_credential(
    db: AsyncSession, user_id: UUID, credential_id: bytes
) -> OwnCredential | None:
    """The credential with this id **of this user**; None when unknown or another user's."""
    row = (
        await db.execute(
            select(
                webauthn_credentials.c.id,
                webauthn_credentials.c.public_key,
                webauthn_credentials.c.backup_eligible,
            ).where(
                webauthn_credentials.c.credential_id == credential_id,
                webauthn_credentials.c.user_id == user_id,
            )
        )
    ).first()
    return None if row is None else OwnCredential(row[0], bytes(row[1]), row[2])


async def record_failure(
    db: AsyncSession, user_id: UUID, ip: str, reason: str, passkey: str | None = None
) -> None:
    """``reauth_failed`` with the category only (section 10)."""
    await record_passkey_failure(db, "reauth_failed", user_id, ip, reason, passkey)


async def complete(
    request: Request, info: SessionInfo, found: OwnCredential, asserted: Asserted, ip: str
) -> Outcome:
    """Raises ``SessionGone`` (all rolled back) when the session was revoked meanwhile."""
    user_id, passkey = info.user_id, str(found.id)
    async with system_session(factory(request)) as db:
        locked = await lock_user(db, user_id)
        if locked is None:
            return Outcome(reason="gone")  # the account was deleted meanwhile; nothing written
        if locked.disabled:  # before the counter, so a disabled account's passkey is not touched
            await record_failure(db, user_id, ip, "disabled", passkey)
            return Outcome(reason="disabled")
        counted = await advance_or_refuse(
            db,
            passkey_id=found.id,
            user_id=user_id,
            asserted=asserted,
            ip=ip,
            failed="reauth_failed",
        )
        if counted.reason is not None:
            return Outcome(reason=counted.reason, notice=counted.notice)
        issued = await rotate_session(
            db,
            info,
            reauth=True,  # never stepup or mfa: a passkey is not a step-up (section 8)
            user_agent=user_agent(request),
            ip=ip,
            mark=request_client_mark(request, ip),
        )
        await record_event(
            db, "reauth", user_id=user_id, ip=ip, detail={"method": METHOD, "passkey": passkey}
        )
    return Outcome(issued=issued)
