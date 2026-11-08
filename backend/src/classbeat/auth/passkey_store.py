"""``webauthn_challenges``, ``webauthn_credentials`` and ``users.webauthn_user_handle`` (ADR 0019
sections 4, 6 and 7, #772).

System tables (guard 8). Every query on a credential filters on the user it is bound to: the
signed-in user's id, never an id from the request alone, so another user's passkey id matches
nothing and the route answers 404 as for an unknown one.

A challenge is found only through the SHA-256 of its flow cookie and taken exactly once with
``DELETE ... RETURNING``; the caller runs the take in a transaction of its own that commits before
anything else (section 6, rule 1).
"""

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.keys import new_token, token_hash
from classbeat.auth.passkey_policy import CHALLENGE_SECONDS, USER_HANDLE_BYTES
from classbeat.auth.passkey_webauthn import NewPasskey
from classbeat.auth.tables import users, webauthn_challenges, webauthn_credentials

CHALLENGE_LIFETIME = timedelta(seconds=CHALLENGE_SECONDS)


@dataclass(frozen=True, slots=True)
class TakenChallenge:
    purpose: str
    challenge: bytes
    user_id: UUID | None
    session_id: UUID | None


@dataclass(frozen=True, slots=True)
class PasskeyRow:
    id: UUID
    label: str
    transports: list[str]
    backup_eligible: bool
    created_at: datetime
    last_used_at: datetime | None


async def create_challenge(
    db: AsyncSession,
    *,
    purpose: str,
    challenge: bytes,
    user_id: UUID | None,
    session_id: UUID | None,
    previous_cookie: str | None,
) -> str:
    """Store a new flow and return its cookie value. The flow of the browser's previous cookie of
    the same purpose goes first, so a browser holds at most one flow per purpose."""
    if previous_cookie:
        await db.execute(
            delete(webauthn_challenges).where(
                webauthn_challenges.c.flow_hash == token_hash(previous_cookie),
                webauthn_challenges.c.purpose == purpose,
            )
        )
    cookie = new_token()
    await db.execute(
        insert(webauthn_challenges).values(
            flow_hash=token_hash(cookie),
            purpose=purpose,
            challenge=challenge,
            user_id=user_id,
            session_id=session_id,
            expires_at=func.now() + CHALLENGE_LIFETIME,
        )
    )
    return cookie


async def take_challenge(db: AsyncSession, cookie: str) -> TakenChallenge | None:
    """Atomic single use: the unexpired row is deleted by the request that reads it."""
    row = (
        await db.execute(
            delete(webauthn_challenges)
            .where(
                webauthn_challenges.c.flow_hash == token_hash(cookie),
                webauthn_challenges.c.expires_at > func.now(),
            )
            .returning(
                webauthn_challenges.c.purpose,
                webauthn_challenges.c.challenge,
                webauthn_challenges.c.user_id,
                webauthn_challenges.c.session_id,
            )
        )
    ).first()
    return None if row is None else TakenChallenge(row[0], bytes(row[1]), row[2], row[3])


async def ensure_user_handle(db: AsyncSession, user_id: UUID) -> bytes | None:
    """The user's WebAuthn user handle, made at the first registration and kept for the account's
    life (section 4). Set only while still NULL, then read back, so two parallel first starts end
    with one handle and both use it. None if the user is gone."""
    await db.execute(
        update(users)
        .where(users.c.id == user_id, users.c.webauthn_user_handle.is_(None))
        .values(webauthn_user_handle=secrets.token_bytes(USER_HANDLE_BYTES))
    )
    handle = (
        await db.execute(select(users.c.webauthn_user_handle).where(users.c.id == user_id))
    ).scalar_one_or_none()
    return None if handle is None else bytes(handle)


async def credential_ids(db: AsyncSession, user_id: UUID) -> list[bytes]:
    """The user's own credential ids (``excludeCredentials``)."""
    rows = await db.execute(
        select(webauthn_credentials.c.credential_id)
        .where(webauthn_credentials.c.user_id == user_id)
        .order_by(webauthn_credentials.c.created_at)
    )
    return [bytes(value) for value in rows.scalars()]


async def count_passkeys(db: AsyncSession, user_id: UUID) -> int:
    found = await db.execute(select(func.count()).where(webauthn_credentials.c.user_id == user_id))
    return int(found.scalar_one())


_COLUMNS = (
    webauthn_credentials.c.id,
    webauthn_credentials.c.label,
    webauthn_credentials.c.transports,
    webauthn_credentials.c.backup_eligible,
    webauthn_credentials.c.created_at,
    webauthn_credentials.c.last_used_at,
)


def _row(row: Sequence[Any]) -> PasskeyRow:
    return PasskeyRow(row[0], row[1], list(row[2]), row[3], row[4], row[5])


async def insert_passkey(
    db: AsyncSession, user_id: UUID, new: NewPasskey, label: str
) -> PasskeyRow | None:
    """Store the passkey for ``user_id``; None when the credential id exists already (for this
    user or another: the caller answers the same either way)."""
    try:
        async with db.begin_nested():
            row = (
                await db.execute(
                    insert(webauthn_credentials)
                    .values(
                        user_id=user_id,
                        credential_id=new.credential_id,
                        public_key=new.public_key,
                        sign_count=new.sign_count,
                        transports=new.transports,
                        backup_eligible=new.backup_eligible,
                        backup_state=new.backup_state,
                        label=label,
                    )
                    .returning(*_COLUMNS)
                )
            ).one()
    except IntegrityError:
        return None  # never logged: its DETAIL holds the credential id
    return _row(row)


async def list_passkeys(db: AsyncSession, user_id: UUID) -> list[PasskeyRow]:
    rows = await db.execute(
        select(*_COLUMNS)
        .where(webauthn_credentials.c.user_id == user_id)
        .order_by(webauthn_credentials.c.created_at, webauthn_credentials.c.id)
    )
    return [_row(row) for row in rows.all()]


async def rename_passkey(
    db: AsyncSession, user_id: UUID, passkey_id: UUID, label: str
) -> PasskeyRow | None:
    """Set the label of one of the user's own passkeys; None for an unknown or foreign id."""
    row = (
        await db.execute(
            update(webauthn_credentials)
            .where(
                webauthn_credentials.c.id == passkey_id,
                webauthn_credentials.c.user_id == user_id,
            )
            .values(label=label)
            .returning(*_COLUMNS)
        )
    ).first()
    return None if row is None else _row(row)


async def delete_passkey(db: AsyncSession, user_id: UUID, passkey_id: UUID) -> str | None:
    """Delete one of the user's own passkeys and return its label; None for an unknown or foreign
    id. The caller holds the user row lock (section 6)."""
    return (
        await db.execute(
            delete(webauthn_credentials)
            .where(
                webauthn_credentials.c.id == passkey_id,
                webauthn_credentials.c.user_id == user_id,
            )
            .returning(webauthn_credentials.c.label)
        )
    ).scalar_one_or_none()
