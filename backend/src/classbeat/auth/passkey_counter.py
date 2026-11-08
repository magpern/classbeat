"""The counter step of a passkey finish, shared by sign-in (#773) and re-auth (#774) (ADR 0019
sections 5, 7 and 8).

``advance_or_refuse`` runs inside the finish's one write transaction, after ``lock_user`` (section
6, rule 2), on the credential **and its user**: the sign-in's credential user, the re-auth's
session user. The conditional ``UPDATE`` (``advance_counter``) sets the count, ``last_used_at``
and the backup state. No row back:

- the credential is gone (removed, or deleted by a reset that committed first): ``gone``, nothing
  written;
- it still exists, so the counter regressed: ``passkey_counter_regressed`` and the finish's
  failure event (``sign_in_failed`` or ``reauth_failed`` with the category ``counter``) are
  written, the user is mailed at most once a day per passkey, the passkey stays and the caller
  commits with no session.

The address and label of that mail are read under the lock, so an email change committed meanwhile
is honoured (#783 review N3).
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.events import record_event
from classbeat.auth.passkey_assertion import Asserted
from classbeat.auth.passkey_signin_store import (
    advance_counter,
    regressed_contact,
    regression_mailed_recently,
)
from classbeat.emails.notices import Notice, happened

METHOD = "passkey"
FailedKind = Literal["sign_in_failed", "reauth_failed"]


@dataclass(frozen=True, slots=True)
class Counted:
    """``reason`` None when the counter advanced; else ``gone`` or ``counter``, with the mail to
    send (``notice``) if any."""

    reason: Literal["gone", "counter"] | None = None
    notice: Notice | None = None


async def record_passkey_failure(
    db: AsyncSession,
    kind: FailedKind,
    user_id: UUID | None,
    ip: str,
    reason: str,
    passkey: str | None = None,
) -> None:
    """The failure event with the category only (section 10): never a label, credential id or
    key; ``passkey`` is the row id."""
    detail = {"method": METHOD, "reason": reason}
    if passkey is not None:
        detail["passkey"] = passkey
    await record_event(db, kind, user_id=user_id, ip=ip, detail=detail)


async def advance_or_refuse(
    db: AsyncSession,
    *,
    passkey_id: UUID,
    user_id: UUID,
    asserted: Asserted,
    ip: str,
    failed: FailedKind,
) -> Counted:
    """The counter rule of section 7 for ``passkey_id`` of ``user_id``; the caller holds
    ``lock_user`` on ``user_id``."""
    if await advance_counter(db, passkey_id, user_id, asserted.sign_count, asserted.backup_state):
        return Counted()
    contact = await regressed_contact(db, passkey_id, user_id)
    if contact is None:
        return Counted(reason="gone")  # removed, or a reset committed first; nothing written
    email, label = contact
    passkey = str(passkey_id)
    mail = not await regression_mailed_recently(db, passkey_id, user_id)
    await record_event(
        db,
        "passkey_counter_regressed",
        user_id=user_id,
        ip=ip,
        detail={"passkey": passkey, "mailed": mail},
    )
    await record_passkey_failure(db, failed, user_id, ip, "counter", passkey)
    notice = happened("passkey_counter_regressed", email, user_id, label=label)
    return Counted(reason="counter", notice=notice if mail else None)
