"""The signed-in user's own security activity: a page of their ``auth_events`` (#678).

ADR 0003 amendment of 2026-11-07. The list is the user's view of the rows the data export already
holds (ADR 0014), with less in it:

- **Only the caller's rows.** Every query filters on the ``user_id`` of the request's
  ``UserContext``; there is no parameter that names a user. The page cursor carries a time and an
  event id and only bounds that same filter, so a forged or foreign cursor shows nothing more than
  the caller's own older rows.
- **Only the kinds a person can act on** (``SHOWN_KINDS``, an allowlist): a new kind stays hidden
  until it is named here and worded in every catalogue, and operator rows (``connections_wiped``,
  the CLI role changes) and connection events stay out.
- **Only these fields:** the kind, the time, the address (``ip``, as the export holds it), the
  sign-in provider for the kinds that record one (an allowlist of provider names) and whether a
  sign-out was made from another session. Never the rest of ``detail``.

Rows are purged after 30 days (``purge``), so a page never reaches further back.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.anomaly import EVENT_KIND as LOCATION_CHANGED
from classbeat.auth.oauth_providers import GITHUB, GOOGLE, MICROSOFT
from classbeat.auth.tables import auth_events

SHOWN_KINDS: frozenset[str] = frozenset(
    {
        "sign_in",
        "sign_in_failed",
        "sign_out",
        "sign_out_all",
        "reauth",
        "reauth_failed",
        "signup_confirmed",
        "password_changed",
        "password_reset",
        "email_change_requested",
        "email_changed",
        "identity_linked",
        "identity_unlinked",
        "totp_enrolled",
        "totp_replaced",
        "totp_removed",
        "recovery_codes_regenerated",
        "mfa_failed",
        "mfa_reset",
        LOCATION_CHANGED,
    }
)
PROVIDERS: frozenset[str] = frozenset({GOOGLE, GITHUB, MICROSOFT})
OTHER_SESSION_KEY = "from_session"  # set on a sign-out made from another session's list

PAGE_DEFAULT = 20
PAGE_MAX = 100
CURSOR_PATTERN = r"^[0-9]{1,19}_[0-9a-f]{32}$"
_CURSOR = re.compile(CURSOR_PATTERN)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class ActivityItem:
    id: UUID
    kind: str
    at: datetime
    ip: str | None
    provider: str | None
    other_session: bool


@dataclass(frozen=True, slots=True)
class ActivityPage:
    items: list[ActivityItem]
    next_cursor: str | None  # None: no older rows


def encode_cursor(at: datetime, event_id: UUID) -> str:
    """The position after ``(at, event_id)``: whole microseconds since the epoch and the id."""
    return f"{(at - _EPOCH) // _MICROSECOND}_{event_id.hex}"


def decode_cursor(value: str) -> tuple[datetime, UUID] | None:
    """The ``(at, id)`` of a cursor, or None when it is not one ``encode_cursor`` could make."""
    if not _CURSOR.fullmatch(value):
        return None
    micros, hex_id = value.split("_")
    try:
        return _EPOCH + int(micros) * _MICROSECOND, UUID(hex=hex_id)
    except OverflowError:
        return None


def _provider(detail: Any) -> str | None:
    found = detail.get("provider") if isinstance(detail, dict) else None
    return found if isinstance(found, str) and found in PROVIDERS else None


def _other_session(kind: str, detail: Any) -> bool:
    return kind == "sign_out" and isinstance(detail, dict) and OTHER_SESSION_KEY in detail


async def list_activity(
    db: AsyncSession, user_id: UUID, *, limit: int, before: tuple[datetime, UUID] | None
) -> ActivityPage:
    """The user's own shown events, newest first, at most ``limit`` (1 to ``PAGE_MAX``), older
    than ``before`` when given. Ties in time are ordered by id, so paging never skips a row."""
    if not 1 <= limit <= PAGE_MAX:
        raise ValueError("limit out of range")
    statement = (
        select(
            auth_events.c.id,
            auth_events.c.kind,
            auth_events.c.at,
            auth_events.c.ip,
            auth_events.c.detail,
        )
        .where(auth_events.c.user_id == user_id, auth_events.c.kind.in_(sorted(SHOWN_KINDS)))
        .order_by(auth_events.c.at.desc(), auth_events.c.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        statement = statement.where(tuple_(auth_events.c.at, auth_events.c.id) < tuple_(*before))
    rows = (await db.execute(statement)).all()
    items = [
        ActivityItem(
            id=row.id,
            kind=row.kind,
            at=row.at,
            ip=row.ip,
            provider=_provider(row.detail),
            other_session=_other_session(row.kind, row.detail),
        )
        for row in rows[:limit]
    ]
    more = len(rows) > limit
    return ActivityPage(items, encode_cursor(items[-1].at, items[-1].id) if more else None)
