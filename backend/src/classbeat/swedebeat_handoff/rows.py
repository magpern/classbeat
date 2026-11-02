"""The ``swedebeat_handoffs`` row (ADR 0016 section 7).

Statements take the caller's session: a web ``user_session`` or a job's fenced transaction.
This module opens neither. Every statement also filters on ``owner_id``.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.swedebeat_handoff.config import TTL_HOURS

_LIVE = text(
    "SELECT id, class_id, match_revision_id, state, mixes_before, mix_id, verdict, detail, "
    "checked_at, expires_at, expires_at <= now() AS expired "
    "FROM swedebeat_handoffs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND superseded_at IS NULL"
)
_BY_ID = text(
    "SELECT id, class_id, match_revision_id, state, mixes_before, mix_id, verdict, detail, "
    "checked_at, expires_at, expires_at <= now() AS expired "
    "FROM swedebeat_handoffs WHERE owner_id = :owner_id AND id = :id "
    "AND superseded_at IS NULL"
)
_SUPERSEDE = text(
    "UPDATE swedebeat_handoffs SET superseded_at = now() "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND superseded_at IS NULL "
    "AND (expires_at <= now() OR match_revision_id <> :match_id)"
)
_INSERT = text(
    "INSERT INTO swedebeat_handoffs (owner_id, class_id, match_revision_id, state, "
    "mixes_before, expires_at) VALUES (:owner_id, :class_id, :match_id, 'open', "
    "CAST(:mixes_before AS jsonb), now() + make_interval(hours => :hours)) "
    "RETURNING id, class_id, match_revision_id, state, mixes_before, mix_id, verdict, detail, "
    "checked_at, expires_at, false AS expired"
)
_CHECKS = text(
    "SELECT count(*) FROM jobs WHERE owner_id = :owner_id AND kind = 'swedebeat_verify_mix' "
    "AND created_at > now() - interval '1 hour'"
)
_OLDEST = text(
    "SELECT extract(epoch FROM (now() - min(created_at))) FROM jobs "
    "WHERE owner_id = :owner_id AND kind = 'swedebeat_verify_mix' "
    "AND created_at > now() - interval '1 hour'"
)
_SAVE = text(
    "UPDATE swedebeat_handoffs SET state = :state, verdict = :verdict, mix_id = :mix_id, "
    "detail = CAST(:detail AS jsonb), checked_at = now() "
    "WHERE owner_id = :owner_id AND id = :id AND superseded_at IS NULL AND expires_at > now() "
    "AND state <> 'verified' RETURNING id"
)
_VERIFY = text(
    "UPDATE swedebeat_handoffs SET state = 'verified', checked_at = coalesce(checked_at, now()) "
    "WHERE owner_id = :owner_id AND id = :id AND superseded_at IS NULL AND expires_at > now() "
    "AND state = 'checked' AND mix_id = :mix_id "
    "AND verdict IN ('too_short', 'too_long') RETURNING id"
)


@dataclass(frozen=True, slots=True)
class Handoff:
    id: UUID
    class_id: UUID
    match_revision_id: UUID
    state: str
    mixes_before: tuple[str, ...]
    mix_id: str | None
    verdict: str | None
    detail: dict[str, Any]
    checked_at: datetime | None
    expires_at: datetime
    expired: bool


def _ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _detail(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _row(row: Any) -> Handoff:
    return Handoff(
        row.id,
        row.class_id,
        row.match_revision_id,
        str(row.state),
        _ids(row.mixes_before),
        row.mix_id if isinstance(row.mix_id, str) else None,
        row.verdict if isinstance(row.verdict, str) else None,
        _detail(row.detail),
        row.checked_at,
        row.expires_at,
        bool(row.expired),
    )


async def live(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Handoff | None:
    row = (await db.execute(_LIVE, {"owner_id": owner_id, "class_id": class_id})).first()
    return None if row is None else _row(row)


async def by_id(db: AsyncSession, owner_id: UUID, handoff_id: UUID) -> Handoff | None:
    row = (await db.execute(_BY_ID, {"owner_id": owner_id, "id": handoff_id})).first()
    return None if row is None else _row(row)


async def supersede_stale(db: AsyncSession, owner_id: UUID, class_id: UUID, match_id: UUID) -> None:
    """Retire an expired row, or one whose match is no longer the approved one. Never extends it."""
    await db.execute(
        _SUPERSEDE,
        {"owner_id": owner_id, "class_id": class_id, "match_id": match_id},
    )


async def insert_open(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    match_id: UUID,
    mixes_before: list[str],
) -> Handoff:
    row = (
        await db.execute(
            _INSERT,
            {
                "owner_id": owner_id,
                "class_id": class_id,
                "match_id": match_id,
                "mixes_before": json.dumps(mixes_before),
                "hours": TTL_HOURS,
            },
        )
    ).one()
    return _row(row)


async def checks_this_hour(db: AsyncSession, owner_id: UUID) -> tuple[int, int]:
    """How many verify jobs this hour, and the age in seconds of the oldest of them (0 if none)."""
    params = {"owner_id": owner_id}
    count = int((await db.execute(_CHECKS, params)).scalar_one())
    age = (await db.execute(_OLDEST, params)).scalar_one()
    return count, 0 if age is None else int(age)


async def save_result(
    db: AsyncSession,
    owner_id: UUID,
    handoff_id: UUID,
    *,
    state: str,
    verdict: str | None,
    mix_id: str | None,
    detail: dict[str, Any],
) -> bool:
    """Write the check's result. False when the row expired, was superseded, or is already
    verified: nothing is changed then."""
    found = (
        await db.execute(
            _SAVE,
            {
                "owner_id": owner_id,
                "id": handoff_id,
                "state": state,
                "verdict": verdict,
                "mix_id": mix_id,
                "detail": json.dumps(detail),
            },
        )
    ).first()
    return found is not None


async def mark_verified(db: AsyncSession, owner_id: UUID, handoff_id: UUID, mix_id: str) -> bool:
    """The user's override of a length verdict on the mix the check already analysed."""
    found = (
        await db.execute(_VERIFY, {"owner_id": owner_id, "id": handoff_id, "mix_id": mix_id})
    ).first()
    return found is not None
