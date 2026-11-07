"""The sign-in finish's queries on ``webauthn_credentials`` (ADR 0019 sections 5 and 7, #773).

System tables (guard 8). Sign-in is the one place a credential is found by its id alone, because
no user is known yet; the finish then requires the user handle to match, and every write after
that names the credential's user too.

The counter rule is one conditional ``UPDATE`` (section 7), run by the caller after
``lock_user``: the new count must be greater than the stored one, or both are 0. No row back
means the credential is gone (removed, or deleted by a reset that committed first) or the counter
regressed; ``credential_exists`` tells the two apart under the same lock.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import BigInteger, and_, exists, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import auth_events, users, webauthn_credentials

REGRESSION_MAIL_EVERY = timedelta(days=1)  # at most one mail a day per passkey (section 7)


@dataclass(frozen=True, slots=True)
class SignInCredential:
    """A credential found by its id, with what the finish checks before any lock."""

    id: UUID
    user_id: UUID
    public_key: bytes
    backup_eligible: bool
    user_handle: bytes | None
    email: str
    label: str


async def find_credential(db: AsyncSession, credential_id: bytes) -> SignInCredential | None:
    """The credential with this id and its user's handle and address; None when unknown."""
    row = (
        await db.execute(
            select(
                webauthn_credentials.c.id,
                webauthn_credentials.c.user_id,
                webauthn_credentials.c.public_key,
                webauthn_credentials.c.backup_eligible,
                users.c.webauthn_user_handle,
                users.c.email,
                webauthn_credentials.c.label,
            )
            .join(users, users.c.id == webauthn_credentials.c.user_id)
            .where(webauthn_credentials.c.credential_id == credential_id)
        )
    ).first()
    if row is None:
        return None
    handle = None if row[4] is None else bytes(row[4])
    return SignInCredential(row[0], row[1], bytes(row[2]), row[3], handle, row[5], row[6])


async def advance_counter(
    db: AsyncSession, passkey_id: UUID, user_id: UUID, sign_count: int, backup_state: bool
) -> bool:
    """The counter rule of section 7, with ``last_used_at`` and the backup state. True when the
    row was updated; False when the credential is gone or the counter did not advance."""
    new = literal(sign_count, BigInteger)
    found = await db.execute(
        update(webauthn_credentials)
        .where(
            webauthn_credentials.c.id == passkey_id,
            webauthn_credentials.c.user_id == user_id,
            or_(
                webauthn_credentials.c.sign_count < new,
                and_(webauthn_credentials.c.sign_count == 0, new == 0),
            ),
        )
        .values(sign_count=sign_count, last_used_at=func.now(), backup_state=backup_state)
        .returning(webauthn_credentials.c.id)
    )
    return found.first() is not None


async def credential_exists(db: AsyncSession, passkey_id: UUID, user_id: UUID) -> bool:
    found = await db.execute(
        select(webauthn_credentials.c.id).where(
            webauthn_credentials.c.id == passkey_id, webauthn_credentials.c.user_id == user_id
        )
    )
    return found.first() is not None


async def regression_mailed_recently(db: AsyncSession, passkey_id: UUID, user_id: UUID) -> bool:
    """Whether a ``passkey_counter_regressed`` event that mailed the user (``mailed: true`` in its
    detail) was written for this passkey in the last day: at most one mail a day per passkey,
    however often it regresses. Called under ``lock_user``, before the new event, so two parallel
    regressions mail once."""
    found = await db.execute(
        select(
            exists().where(
                auth_events.c.kind == "passkey_counter_regressed",
                auth_events.c.user_id == user_id,
                auth_events.c.detail["passkey"].astext == str(passkey_id),
                auth_events.c.detail["mailed"].astext == "true",
                auth_events.c.at > func.now() - REGRESSION_MAIL_EVERY,
            )
        )
    )
    return bool(found.scalar_one())
