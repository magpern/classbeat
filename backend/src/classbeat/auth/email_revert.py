"""Reverting an email change and locking the account down (ADR 0003 amendment of 2026-11-19).

The "this wasn't me" link in the notice to the old address (#1265) lands here. Whoever changed the
address had a session with recent authentication, so they may have planted ways back in: a
password they know, a linked sign-in method, a passkey, an authenticator. Restoring the address
alone would leave all of that, so the revert is followed, in the same transaction, by a lockdown:

- the address goes back (the unique key rechecks that it is still free);
- the password is cleared, and every linked identity, the TOTP, the recovery codes and every
  passkey are deleted;
- every session is revoked (``revoke_user_sessions``, which also queues the player logout
  events of ADR 0034 section 2) and the known devices are forgotten;
- every outstanding mail token is deleted, the revert's own included, and one fresh
  ``reset_password`` token is issued for the mail to the restored address.

The user row lock is taken first, before the token is consumed (ADR 0019 section 6), so a
sign-in, re-authentication, link, password change or reset request in flight either commits first
and is undone here or waits and finds its session gone or the address moved (#1265 review).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.devices import forget_devices
from classbeat.auth.events import record_event
from classbeat.auth.passkey_recovery import remove_passkeys_except
from classbeat.auth.sessions import revoke_user_sessions
from classbeat.auth.tables import identities, mfa_recovery_codes, mfa_totp, users
from classbeat.auth.tokens import (
    RESET_LIFETIME,
    REVERT_PURPOSE,
    consume_token,
    delete_outstanding_tokens,
    issue_token,
    peek_token,
)
from classbeat.auth.userlock import lock_user
from classbeat.emailnorm import normalize_email

RESET_PURPOSE = "reset_password"
EVENT_KIND = "email_reverted"


class RevertRefused(Exception):
    """``invalid_token`` (unknown, used, expired, foreign purpose or stale) or ``email_taken``.
    Raised inside the transaction, so the token is not consumed."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


INVALID = (400, "invalid_token")
TAKEN = (409, "email_taken")


@dataclass(frozen=True, slots=True)
class Reverted:
    user_id: UUID
    email: str  # the restored address
    reset_token: str


async def revert_email_change(db: AsyncSession, raw_token: str, *, ip: str | None) -> Reverted:
    """Consume the revert token, restore the address and lock the account down; raise
    ``RevertRefused`` for anything else. The caller commits, then sends the mail."""
    # The user row lock before the token, as every path that consumes a token of this user now
    # takes it (ADR 0019 section 6): taking it after would deadlock against a reset completion
    # or an email confirmation that holds its own token row and waits for this lock.
    live = await peek_token(db, REVERT_PURPOSE, raw_token)
    if live is None or await lock_user(db, live.user_id) is None:
        raise RevertRefused(*INVALID)  # unknown, used, expired, or the account was deleted
    token = await consume_token(db, REVERT_PURPOSE, raw_token, user_id=live.user_id)
    if token is None or token.new_email is None or token.email_at_issue is None:
        raise RevertRefused(*INVALID)
    current = (
        await db.execute(select(users.c.email).where(users.c.id == token.user_id))
    ).scalar_one()
    if current != token.email_at_issue:
        raise RevertRefused(*INVALID)  # the address changed again since the notice
    restored = token.new_email
    try:
        async with db.begin_nested():
            await db.execute(
                update(users)
                .where(users.c.id == token.user_id)
                .values(
                    email=restored,
                    email_normalized=normalize_email(restored),
                    password_hash=None,
                )
            )
    except IntegrityError:
        raise RevertRefused(*TAKEN) from None  # never logged: its DETAIL holds the address
    detail = await _lock_down(db, token.user_id)
    await delete_outstanding_tokens(db, token.user_id)
    reset_token = await issue_token(db, RESET_PURPOSE, token.user_id, RESET_LIFETIME)
    await record_event(db, EVENT_KIND, user_id=token.user_id, ip=ip, detail=detail)
    return Reverted(token.user_id, restored, reset_token)


async def _lock_down(db: AsyncSession, user_id: UUID) -> dict[str, int]:
    """Every credential and session of the user goes (the password was cleared by the caller)."""
    removed = await db.execute(
        delete(identities).where(identities.c.user_id == user_id).returning(identities.c.id)
    )
    methods = len(removed.all())
    await db.execute(delete(mfa_totp).where(mfa_totp.c.user_id == user_id))
    await db.execute(delete(mfa_recovery_codes).where(mfa_recovery_codes.c.user_id == user_id))
    passkeys = await remove_passkeys_except(db, user_id, ())
    await revoke_user_sessions(db, user_id)
    await forget_devices(db, user_id)
    return {"methods_removed": methods, "passkeys_removed": passkeys}
