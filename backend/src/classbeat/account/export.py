"""The data export (ADR 0014 section 2): one JSON document of the user's own data.

Everything is read in the user's own ``user_session`` (row-level security) and every statement
also filters on ``owner_id``, so a lost policy still shows nobody else's row
(``tests/test_account_export.py`` runs it on a superuser factory). The account tables come from
``classbeat.auth.account_export`` (guard 8), the connections as status only from
``classbeat.connections``. Admin audit rows (what an operator did to the account) are not here:
the table is operator-only (ADR 0011 section 5), see ADR 0014 section 2.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat import auth
from classbeat.account.registry import SECTIONS, Section
from classbeat.connections import connection_statuses
from classbeat.context import UserContext, require_context
from classbeat.db import user_session

FORMAT = "classbeat-export"
VERSION = 1

NOT_INCLUDED = [
    "Passwords, password hashes, session tokens, TOTP secrets and recovery codes.",
    "Tokens, keys and Client IDs of your MOWL, Spotify and Swedebeat connections: only status.",
    "Internal bookkeeping (request hashes, idempotency keys, lease and worker fields).",
    "What an operator did to your account (the admin audit log); ask the operator for it.",
    "Workouts saved to MOWL and playlists made in your Spotify: they are yours, in those services.",
]


_COUNT_MIX_MAPS = text("SELECT count(*) FROM swedebeat_mix_maps WHERE owner_id = :owner_id")


def _read(section: Section) -> Any:
    # Table, key and columns come from the fixed registry (validated when it is built); only the
    # owner is a parameter.
    return text(
        f"SELECT to_jsonb(t) - CAST(:omit AS text[]) AS row FROM {section.table} t "  # noqa: S608
        f"WHERE t.owner_id = :owner_id ORDER BY {section.order}"
    )


async def _rows(db: AsyncSession, owner_id: Any, section: Section) -> list[dict[str, Any]]:
    result = await db.execute(_read(section), {"owner_id": owner_id, "omit": sorted(section.omit)})
    return [row[0] for row in result.all()]


async def build_export(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """The export document, or ``None`` when the account no longer exists."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        account = await auth.account_export(db, ctx.user_id)
        if not account:
            return None
        document: dict[str, Any] = {
            "format": FORMAT,
            "version": VERSION,
            "generated_at": (now or datetime.now(UTC)).isoformat(),
            "account": account["profile"],
            "sign_in_methods": account["sign_in_methods"],
            "two_factor": account["two_factor"],
            "sessions": account["sessions"],
            "known_devices": account["known_devices"],
            "security_events": account["security_events"],
            "connections": [
                {
                    "provider": status.provider,
                    "status": status.status,
                    "last_error": status.last_error,
                    "connected_at": None
                    if status.connected_at is None
                    else status.connected_at.isoformat(),
                    "last_used_at": None
                    if status.last_used_at is None
                    else status.last_used_at.isoformat(),
                }
                for status in await connection_statuses(db, ctx.user_id)
            ],
        }
        for section in SECTIONS:
            document[section.key] = await _rows(db, ctx.user_id, section)
        document["swedebeat_mix_analyses"] = (
            await db.execute(_COUNT_MIX_MAPS, {"owner_id": ctx.user_id})
        ).scalar_one()
        document["not_included"] = NOT_INCLUDED
        return document
