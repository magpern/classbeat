"""TOTP enrollment, verification and recovery codes (ADR 0003 sections 6 to 8).

All functions take the caller's ``AsyncSession`` (a ``system_session``) and filter on the
``user_id`` of the request's own session. Replay protection and recovery-code single use are
conditional ``UPDATE`` statements, so concurrent submissions of one code let exactly one win.

A pending enrollment is not stored: ``seal_pending`` returns the new secret encrypted
(AES-256-GCM, AAD ``user_id|totp-pending``, 10 minutes) for the client to send back with the
first code. So replacing a confirmed TOTP never leaves the account half-enrolled, and a
``mfa_totp`` row is always a confirmed one.
"""

import base64
import logging
import secrets
import time
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth import totp
from classbeat.auth.keys import (
    NONCE_LENGTH,
    Keys,
    decrypt_totp_secret,
    encrypt_totp_secret,
    hmac_digest,
)
from classbeat.auth.tables import auth_events, mfa_recovery_codes, mfa_totp

logger = logging.getLogger(__name__)

PENDING_LIFETIME = 600  # seconds
FAILURE_MAIL_THRESHOLD = 5  # failures in an hour

Method = Literal["totp", "recovery"]


def _pending_aad(user_id: UUID) -> bytes:
    return f"{user_id}|totp-pending".encode()


def seal_pending(keys: Keys, user_id: UUID, secret: str) -> str:
    """The not-yet-confirmed secret, encrypted for the client to send back with the first code."""
    nonce = secrets.token_bytes(NONCE_LENGTH)
    plain = f"{int(time.time()) + PENDING_LIFETIME}|{secret}".encode()
    blob = AESGCM(keys.totp).encrypt(nonce, plain, _pending_aad(user_id))
    return base64.urlsafe_b64encode(nonce + blob).decode()


def open_pending(keys: Keys, user_id: UUID, sealed: str) -> str | None:
    """The secret, or ``None`` when the blob is not this user's, was altered or has expired."""
    try:
        raw = base64.urlsafe_b64decode(sealed.encode())
        plain = AESGCM(keys.totp).decrypt(
            raw[:NONCE_LENGTH], raw[NONCE_LENGTH:], _pending_aad(user_id)
        )
        expires, secret = plain.decode().split("|", 1)
        return secret if int(expires) > time.time() else None
    except (InvalidTag, ValueError):
        return None


async def recovery_codes_left(db: AsyncSession, user_id: UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(mfa_recovery_codes)
            .where(mfa_recovery_codes.c.user_id == user_id, mfa_recovery_codes.c.used_at.is_(None))
        )
    ).scalar_one()


def _recovery_hash(keys: Keys, user_id: UUID, code: str) -> bytes:
    return hmac_digest(keys.recovery_codes, str(user_id), totp.normalize_recovery_code(code))


async def regenerate_recovery_codes(db: AsyncSession, keys: Keys, user_id: UUID) -> list[str]:
    """Ten new codes; the old ones stop working. Shown once, stored as an HMAC."""
    codes = totp.new_recovery_codes()
    await db.execute(delete(mfa_recovery_codes).where(mfa_recovery_codes.c.user_id == user_id))
    await db.execute(
        insert(mfa_recovery_codes),
        [{"user_id": user_id, "code_hash": _recovery_hash(keys, user_id, c)} for c in codes],
    )
    return codes


async def confirm_enrollment(
    db: AsyncSession, keys: Keys, user_id: UUID, secret: str, code: str, *, replacing: bool
) -> list[str] | None:
    """Store the secret (AES-256-GCM) once ``code`` is valid for it, and issue recovery codes.

    ``replacing`` is what the caller's step-up check assumed: whether a confirmed TOTP exists.
    If that changed meanwhile, nothing is stored (``None``), as for a wrong code.
    """
    step = totp.match_step(secret, code)
    if step is None:
        return None
    current = (
        await db.execute(
            select(mfa_totp.c.confirmed_at).where(mfa_totp.c.user_id == user_id).with_for_update()
        )
    ).first()
    if (current is not None and current.confirmed_at is not None) != replacing:
        return None
    ciphertext, nonce, key_id = encrypt_totp_secret(keys, user_id, secret.encode())
    values = {
        "secret_ciphertext": ciphertext,
        "nonce": nonce,
        "key_id": key_id,
        "confirmed_at": func.now(),
        "last_step": step,
    }
    await db.execute(
        pg_insert(mfa_totp)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(index_elements=[mfa_totp.c.user_id], set_=values)
    )
    return await regenerate_recovery_codes(db, keys, user_id)


async def remove_totp(db: AsyncSession, user_id: UUID) -> bool:
    await db.execute(delete(mfa_recovery_codes).where(mfa_recovery_codes.c.user_id == user_id))
    result = await db.execute(
        delete(mfa_totp).where(mfa_totp.c.user_id == user_id).returning(mfa_totp.c.id)
    )
    return result.first() is not None


async def _verify_totp(db: AsyncSession, keys: Keys, user_id: UUID, code: str) -> bool:
    row = (
        await db.execute(
            select(mfa_totp.c.secret_ciphertext, mfa_totp.c.nonce, mfa_totp.c.key_id).where(
                mfa_totp.c.user_id == user_id, mfa_totp.c.confirmed_at.is_not(None)
            )
        )
    ).first()
    if row is None:
        return False
    try:
        secret = decrypt_totp_secret(
            keys, user_id, row.secret_ciphertext, row.nonce, row.key_id
        ).decode()
    except ValueError:
        logger.error("a stored TOTP secret does not decrypt for its user")
        return False
    step = totp.match_step(secret, code)
    if step is None:
        return False
    # Replay protection: a step is accepted once. The loser of a race updates no row.
    claimed = await db.execute(
        update(mfa_totp)
        .where(mfa_totp.c.user_id == user_id, mfa_totp.c.last_step < step)
        .values(last_step=step)
        .returning(mfa_totp.c.id)
    )
    return claimed.first() is not None


async def _use_recovery_code(db: AsyncSession, keys: Keys, user_id: UUID, code: str) -> bool:
    used = await db.execute(
        update(mfa_recovery_codes)
        .where(
            mfa_recovery_codes.c.user_id == user_id,
            mfa_recovery_codes.c.code_hash == _recovery_hash(keys, user_id, code),
            mfa_recovery_codes.c.used_at.is_(None),
        )
        .values(used_at=func.now())
        .returning(mfa_recovery_codes.c.id)
    )
    return used.first() is not None


async def check_second_factor(
    db: AsyncSession, keys: Keys, user_id: UUID, code: str
) -> Method | None:
    """A TOTP code (six digits) or a recovery code, consumed on success. ``None`` otherwise."""
    code = code.strip()
    if totp.is_totp_code(code):
        return "totp" if await _verify_totp(db, keys, user_id, code) else None
    if await _use_recovery_code(db, keys, user_id, code):
        return "recovery"
    return None


async def record_failure(db: AsyncSession, user_id: UUID, ip: str | None, purpose: str) -> bool:
    """Write ``mfa_failed``. True when this is the fifth in the last hour (mail the user)."""
    await db.execute(
        insert(auth_events).values(
            kind="mfa_failed", user_id=user_id, ip=ip, detail={"purpose": purpose}
        )
    )
    count = (
        await db.execute(
            select(func.count())
            .select_from(auth_events)
            .where(
                auth_events.c.user_id == user_id,
                auth_events.c.kind == "mfa_failed",
                auth_events.c.at > func.now() - func.make_interval(0, 0, 0, 0, 1),
            )
        )
    ).scalar_one()
    return count == FAILURE_MAIL_THRESHOLD
