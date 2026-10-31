"""The account's own rows for the data export (ADR 0014 section 2).

Guard 8 keeps every statement on the account tables in this package, so ``classbeat.account``
calls ``account_export`` with the session it holds. Every statement filters on the user's id.
Only what the user could see about themselves is returned:

- the profile (id, email, role, when it was created and verified, whether it has a password);
- the linked sign-in providers (provider, the provider's subject, the address at linking);
- the signed-in devices (when, user agent and address, never a token or its hash), the known
  devices (dates only);
- two-factor status (whether it is on, when confirmed, how many recovery codes are unused;
  never the secret, a code or a hash);
- the user's security events (kind, address, time, detail).

Never ``password_hash``, a token hash or any ciphertext.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.tables import (
    auth_events,
    identities,
    known_devices,
    mfa_recovery_codes,
    mfa_totp,
    sessions,
    users,
)


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


async def account_export(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """The sections of the export that live in the account tables; ``{}`` for an unknown user."""
    profile = (
        await db.execute(
            select(
                users.c.id,
                users.c.email,
                users.c.role,
                users.c.email_verified_at,
                users.c.created_at,
                users.c.disabled_at,
                users.c.password_hash.is_not(None).label("has_password"),
            ).where(users.c.id == user_id)
        )
    ).first()
    if profile is None:
        return {}
    linked = (
        await db.execute(
            select(
                identities.c.provider,
                identities.c.subject,
                identities.c.email_at_link,
                identities.c.created_at,
            )
            .where(identities.c.user_id == user_id)
            .order_by(identities.c.created_at, identities.c.id)
        )
    ).all()
    signed_in = (
        await db.execute(
            select(
                sessions.c.created_at,
                sessions.c.last_seen_at,
                sessions.c.expires_at,
                sessions.c.user_agent,
                sessions.c.ip,
            )
            .where(sessions.c.user_id == user_id)
            .order_by(sessions.c.created_at, sessions.c.id)
        )
    ).all()
    devices = (
        await db.execute(
            select(known_devices.c.created_at, known_devices.c.last_used_at)
            .where(known_devices.c.user_id == user_id)
            .order_by(known_devices.c.created_at, known_devices.c.id)
        )
    ).all()
    confirmed = (
        await db.execute(select(mfa_totp.c.confirmed_at).where(mfa_totp.c.user_id == user_id))
    ).first()
    unused_codes = (
        await db.execute(
            select(func.count())
            .select_from(mfa_recovery_codes)
            .where(mfa_recovery_codes.c.user_id == user_id, mfa_recovery_codes.c.used_at.is_(None))
        )
    ).scalar_one()
    events = (
        await db.execute(
            select(auth_events.c.kind, auth_events.c.ip, auth_events.c.at, auth_events.c.detail)
            .where(auth_events.c.user_id == user_id)
            .order_by(auth_events.c.at, auth_events.c.id)
        )
    ).all()
    return {
        "profile": {
            "id": str(profile.id),
            "email": profile.email,
            "role": profile.role,
            "email_verified_at": _iso(profile.email_verified_at),
            "created_at": _iso(profile.created_at),
            "disabled_at": _iso(profile.disabled_at),
            "has_password": bool(profile.has_password),
        },
        "sign_in_methods": [
            {
                "provider": row.provider,
                "subject": row.subject,
                "email_at_link": row.email_at_link,
                "linked_at": _iso(row.created_at),
            }
            for row in linked
        ],
        "sessions": [
            {
                "created_at": _iso(row.created_at),
                "last_seen_at": _iso(row.last_seen_at),
                "expires_at": _iso(row.expires_at),
                "user_agent": row.user_agent,
                "ip": row.ip,
            }
            for row in signed_in
        ],
        "known_devices": [
            {"first_seen_at": _iso(row.created_at), "last_used_at": _iso(row.last_used_at)}
            for row in devices
        ],
        "two_factor": {
            "enabled": confirmed is not None and confirmed.confirmed_at is not None,
            "confirmed_at": None if confirmed is None else _iso(confirmed.confirmed_at),
            "unused_recovery_codes": unused_codes,
        },
        "security_events": [
            {"kind": row.kind, "ip": row.ip, "at": _iso(row.at), "detail": row.detail}
            for row in events
        ],
    }
