"""A class's dismissed soft rules (ADR 0017 section 4), split out of
``classbeat.classes.service``: one ``user_session``, the class row locked."""

import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.rules.enums import RULE_IDS

Factory = async_sessionmaker[AsyncSession]

MAX_DISMISSED: Final = 32  # also the CHECK of ``classes.dismissed_rules`` (migration 0017)

_LOCK_DISMISSED = text(
    "SELECT dismissed_rules FROM classes WHERE id = :class_id AND owner_id = :owner_id FOR UPDATE"
)
_SET_DISMISSED = text(
    "UPDATE classes SET dismissed_rules = CAST(:rules AS jsonb) "  # no updated_at: not a reorder
    "WHERE id = :class_id AND owner_id = :owner_id"
)


def is_rule_id(value: object) -> bool:
    """A soft-rule id of the closed enum shared with the profile loader (ADR 0017)."""
    return isinstance(value, str) and value in RULE_IDS


@dataclass(frozen=True, slots=True)
class Dismissed:
    """``reason`` is ``None`` when the call went through; ``rules`` is the list afterwards."""

    reason: str | None = None
    rules: tuple[str, ...] = ()


async def set_dismissed(
    factory: Factory, ctx: UserContext, class_id: UUID, rule_id: str, *, dismissed: bool
) -> Dismissed:
    """ADR 0017 section 4: dismiss (``dismissed``) or show again one rule of one class.

    ``rule_id`` must be in ``RULE_IDS`` of ``design.rules.enums`` (``invalid_input`` otherwise,
    checked after the class, so another user's class is ``not_found`` whatever the id). The class
    row is locked, so two calls cannot lose each other's change.
    Both are idempotent; the order is the order of dismissal.
    ``not_found`` (the class, or another user's); ``limit_exceeded`` when a new id would be the
    33rd."""
    require_context(ctx)
    ids = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        row = (await db.execute(_LOCK_DISMISSED, ids)).first()
        if row is None:
            return Dismissed("not_found")
        if not is_rule_id(rule_id):
            return Dismissed("invalid_input")
        current = [r for r in row.dismissed_rules if isinstance(r, str)]
        if dismissed and rule_id not in current:
            current.append(rule_id)
        elif not dismissed:
            current = [r for r in current if r != rule_id]
        if len(current) > MAX_DISMISSED:
            return Dismissed("limit_exceeded")
        if current != list(row.dismissed_rules):
            await db.execute(_SET_DISMISSED, {**ids, "rules": json.dumps(current)})
    return Dismissed(None, tuple(current))
