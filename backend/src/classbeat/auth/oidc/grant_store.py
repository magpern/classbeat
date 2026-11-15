"""``oidc_grants`` and what the provider reads about a user (ADR 0034 section 1).

One grant row per user and client: ``consent_at`` (cleared when the user withdraws it) and
``first_issued_at`` (set at the first ID token, never cleared: the durable marker that the user
has a player account).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.oauth_store import has_password
from classbeat.auth.oidc.schema import oidc_grants
from classbeat.auth.tables import identities, mfa_totp, users, webauthn_credentials


@dataclass(frozen=True, slots=True)
class Person:
    email: str
    email_verified: bool
    role: str
    active: bool


async def person(db: AsyncSession, user_id: UUID) -> Person | None:
    row = (
        await db.execute(
            select(
                users.c.email, users.c.email_verified_at, users.c.role, users.c.disabled_at
            ).where(users.c.id == user_id)
        )
    ).first()
    if row is None:
        return None
    return Person(row.email, row.email_verified_at is not None, row.role, row.disabled_at is None)


async def can_reauthenticate(db: AsyncSession, user_id: UUID, *, role: str, passkeys: bool) -> bool:
    """Any method of ADR 0003 section 6: a password, a linked provider, a passkey (when passkeys
    are on), or an admin's confirmed TOTP."""
    if await has_password(db, user_id):
        return True
    linked = exists().where(identities.c.user_id == user_id)
    keyed = exists().where(webauthn_credentials.c.user_id == user_id)
    totp = exists().where(mfa_totp.c.user_id == user_id, mfa_totp.c.confirmed_at.is_not(None))
    row = (await db.execute(select(linked, keyed, totp))).one()
    return bool(row[0] or (passkeys and row[1]) or (role == "admin" and row[2]))


async def consent_at(db: AsyncSession, user_id: UUID, client_id: str) -> datetime | None:
    return (
        await db.execute(
            select(oidc_grants.c.consent_at).where(
                oidc_grants.c.user_id == user_id, oidc_grants.c.client_id == client_id
            )
        )
    ).scalar_one_or_none()


async def record_consent(db: AsyncSession, user_id: UUID, client_id: str) -> bool:
    """Set ``consent_at`` unless it is set; ``True`` when this is a new consent."""
    row = (
        await db.execute(
            insert(oidc_grants)
            .values(user_id=user_id, client_id=client_id, consent_at=func.now())
            .on_conflict_do_update(
                constraint="oidc_grants_user_client_key",
                set_={"consent_at": func.coalesce(oidc_grants.c.consent_at, func.now())},
            )
            .returning(oidc_grants.c.consent_at == func.now())
        )
    ).scalar_one()
    return bool(row)


async def mark_issued(db: AsyncSession, user_id: UUID, client_id: str) -> None:
    await db.execute(
        insert(oidc_grants)
        .values(user_id=user_id, client_id=client_id, first_issued_at=func.now())
        .on_conflict_do_update(
            constraint="oidc_grants_user_client_key",
            set_={"first_issued_at": func.coalesce(oidc_grants.c.first_issued_at, func.now())},
        )
    )


async def withdraw_consent(db: AsyncSession, user_id: UUID, client_id: str) -> bool:
    result = await db.execute(
        update(oidc_grants)
        .where(
            oidc_grants.c.user_id == user_id,
            oidc_grants.c.client_id == client_id,
            oidc_grants.c.consent_at.is_not(None),
        )
        .values(consent_at=None)
    )
    return bool(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]
