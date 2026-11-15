"""``oidc_codes``: authorization codes (ADR 0034 section 1).

A code is 32 random bytes (base64url); only its SHA-256 is stored. It lives 60 seconds and is
single use: ``redeem_code`` marks it used with one ``UPDATE ... WHERE used_at IS NULL RETURNING``
before any other check, so two parallel redemptions cannot both pass, and a redemption that then
fails (expired, wrong verifier, wrong redirect) still burns it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.keys import new_token, token_hash
from classbeat.auth.oidc.request_policy import Client
from classbeat.auth.oidc.request_store import StoredRequest
from classbeat.auth.oidc.schema import oidc_codes

CODE_LIFETIME = timedelta(seconds=60)
CODE_MAX = 128


@dataclass(frozen=True, slots=True)
class RedeemedCode:
    user_id: UUID
    client_id: str
    redirect_uri: str
    code_challenge: str
    nonce: str
    auth_time: datetime
    expired: bool


@dataclass(frozen=True, slots=True)
class RefusedCode:
    reason: Literal["unknown", "used"]
    user_id: UUID | None


async def issue_code(
    db: AsyncSession, user_id: UUID, client: Client, request: StoredRequest, auth_time: datetime
) -> str:
    code = new_token()
    await db.execute(
        insert(oidc_codes).values(
            code_hash=token_hash(code),
            user_id=user_id,
            client_id=client.client_id,
            redirect_uri=request.redirect_uri,
            code_challenge=request.code_challenge,
            nonce=request.nonce,
            auth_time=auth_time,
            expires_at=func.now() + CODE_LIFETIME,
        )
    )
    return code


async def redeem_code(db: AsyncSession, code: str) -> RedeemedCode | RefusedCode:
    if not code or len(code) > CODE_MAX:
        return RefusedCode("unknown", None)
    digest = token_hash(code)
    row = (
        await db.execute(
            update(oidc_codes)
            .where(oidc_codes.c.code_hash == digest, oidc_codes.c.used_at.is_(None))
            .values(used_at=func.now())
            .returning(
                oidc_codes.c.user_id,
                oidc_codes.c.client_id,
                oidc_codes.c.redirect_uri,
                oidc_codes.c.code_challenge,
                oidc_codes.c.nonce,
                oidc_codes.c.auth_time,
                (oidc_codes.c.expires_at <= func.now()).label("expired"),
            )
        )
    ).first()
    if row is not None:
        return RedeemedCode(**row._asdict())
    used_by = (
        await db.execute(select(oidc_codes.c.user_id).where(oidc_codes.c.code_hash == digest))
    ).scalar_one_or_none()
    return RefusedCode("unknown" if used_by is None else "used", used_by)
