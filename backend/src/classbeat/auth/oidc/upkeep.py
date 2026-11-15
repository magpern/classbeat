"""The provider's part of the hourly purge and of the data export (ADR 0034 section 1, ADR 0014).

- purge: expired requests, and codes that are used or expired (a code lives 60 seconds);
- export: the user's grant rows (client, consent, first sign-in), the columns
  ``classbeat.auth.export.INCLUDE`` lists for ``oidc_grants``.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.oidc.schema import oidc_codes, oidc_grants, oidc_requests

GRANT_COLUMNS = ("client_id", "consent_at", "first_issued_at", "created_at")


async def purge_oidc(db: AsyncSession) -> dict[str, int]:
    now = func.now()
    statements = {
        "oidc_requests": delete(oidc_requests).where(oidc_requests.c.expires_at <= now),
        "oidc_codes": delete(oidc_codes).where(
            or_(oidc_codes.c.expires_at <= now, oidc_codes.c.used_at.is_not(None))
        ),
    }
    counts: dict[str, int] = {}
    for name, statement in statements.items():
        result = await db.execute(statement)
        counts[name] = int(result.rowcount or 0)  # pyright: ignore[reportAttributeAccessIssue]
    return counts


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


async def export_grants(db: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(*(oidc_grants.c[name] for name in GRANT_COLUMNS))
            .where(oidc_grants.c.user_id == user_id)
            .order_by(oidc_grants.c.created_at, oidc_grants.c.client_id)
        )
    ).all()
    return [
        {
            "client": row.client_id,  # not "client_id": that names a Spotify Client ID
            "consent_at": _iso(row.consent_at),
            "first_signed_in_at": _iso(row.first_issued_at),
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]
