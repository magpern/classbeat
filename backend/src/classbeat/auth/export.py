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

**Every column is decided.** ``INCLUDE`` lists the columns each statement reads (the selects are
built from it) and ``EXCLUDE`` the others with the reason; ``NOT_EXPORTED`` gives the reason for
every other system table. ``tests/test_account_export_schema.py`` reads the live schema and fails
when a column or a system table is in neither, so a new column on ``users``, ``sessions`` or
``identities`` cannot be silently left out, and a column with a secret cannot be included by
accident (every ``EXCLUDE`` reason is read in review).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Column, Table, func, select
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

INCLUDE: dict[str, tuple[str, ...]] = {
    "users": ("id", "email", "role", "email_verified_at", "created_at", "disabled_at"),
    "identities": ("provider", "subject", "email_at_link", "created_at"),
    "sessions": ("created_at", "last_seen_at", "expires_at", "user_agent", "ip"),
    "known_devices": ("created_at", "last_used_at"),
    "mfa_totp": ("confirmed_at",),
    "mfa_recovery_codes": ("used_at",),  # only to count the unused ones
    "auth_events": ("kind", "ip", "at", "detail"),
}
EXCLUDE: dict[str, dict[str, str]] = {
    "users": {
        "password_hash": "a secret; only whether there is one is exported (has_password)",
        "email_normalized": "the lookup form of email, derived",
    },
    "identities": {"id": "internal", "user_id": "the user's own id"},
    "sessions": {
        "id": "internal",
        "user_id": "the user's own id",
        "token_hash": "a secret",
        "reauth_at": "internal security timing",
        "mfa_at": "internal security timing",
        "stepup_at": "internal security timing",
    },
    "known_devices": {
        "id": "internal",
        "user_id": "the user's own id",
        "device_hash": "a secret",
    },
    "mfa_totp": {
        "id": "internal",
        "user_id": "the user's own id",
        "secret_ciphertext": "a secret",
        "nonce": "a secret's parameter",
        "key_id": "internal key handling",
        "wrapped_dek": "a wrapped key (ADR 0003 section 8)",
        "dek_nonce": "a wrapped key's parameter",
        "last_step": "internal replay guard",
    },
    "mfa_recovery_codes": {
        "id": "internal",
        "user_id": "the user's own id",
        "code_hash": "a secret",
    },
    "auth_events": {"id": "internal", "user_id": "the user's own id"},
}
NOT_EXPORTED: dict[str, str] = {
    "invites": "operator-issued codes (hash only); the user's link to one is not their data",
    "pending_signups": "sign-ups waiting for a mail link: no account yet; hashes only",
    "auth_tokens": "single-use reset and email-change tokens (hash only), short-lived",
    "oauth_flows": "in-flight sign-in flows: state hashes and a PKCE verifier, short-lived",
    "rate_limits": "counters keyed by an HMAC of the bucket; no user column",
    "alembic_version": "migration bookkeeping",
    "connection_keks": "key-check values of the encryption keys",
    "plans": "the same tiers for every user",
    "redeem_codes": "multi-use plan codes (hash only); the user's redemptions are exported",
    "app_settings": "operator settings, not per user",
    "admin_audit": "operator-only log (ADR 0014 section 2)",
    "model_eval_results": "recorded eval suite runs, not per user",
    "model_routes": "which model each route uses, not per user",
}


def _cols(table: Table) -> list[Column[Any]]:
    """The columns ``INCLUDE`` lists for ``table``, in that order."""
    return [table.c[name] for name in INCLUDE[table.name]]


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


async def account_export(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """The sections of the export that live in the account tables; ``{}`` for an unknown user."""
    profile = (
        await db.execute(
            select(
                *_cols(users),
                users.c.password_hash.is_not(None).label("has_password"),  # the flag, not the hash
            ).where(users.c.id == user_id)
        )
    ).first()
    if profile is None:
        return {}
    linked = (
        await db.execute(
            select(*_cols(identities))
            .where(identities.c.user_id == user_id)
            .order_by(identities.c.created_at, identities.c.id)
        )
    ).all()
    signed_in = (
        await db.execute(
            select(*_cols(sessions))
            .where(sessions.c.user_id == user_id)
            .order_by(sessions.c.created_at, sessions.c.id)
        )
    ).all()
    devices = (
        await db.execute(
            select(*_cols(known_devices))
            .where(known_devices.c.user_id == user_id)
            .order_by(known_devices.c.created_at, known_devices.c.id)
        )
    ).all()
    confirmed = (
        await db.execute(select(*_cols(mfa_totp)).where(mfa_totp.c.user_id == user_id))
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
            select(*_cols(auth_events))
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
