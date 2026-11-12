"""Redeeming a code, steps 2 to 4 of ADR 0011 section 2, inside the caller's transaction: the
user's own earlier redemption is ``already_redeemed``; the conditional count update (one more use
while uses are left, not disabled, not expired; ``redeem_codes_guard`` enforces the same); the
entitlement (``add_entitlement``, ``source = 'code'``, the code's plan, features and days) and the
``code_redemptions`` row.

Opens no session. Two callers, each holding the code's row locked: ``classbeat.plans.redeem``
(``POST /api/account/redeem``, in the user's ``user_session``) and ``classbeat.auth.invite_tier``
(ADR 0033: the code tied to the invite a sign-up consumes, in the sign-up's transaction with
``app.user_id`` set to the new account). Nothing here logs or sees a code's text.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.plans.entitle import add_entitlement

_BY_INVITE = text("SELECT id FROM redeem_codes WHERE invite_id = :i FOR UPDATE")
_MINE = text("SELECT EXISTS (SELECT 1 FROM code_redemptions WHERE owner_id = :o AND code_id = :c)")
_TAKE = text(
    "UPDATE redeem_codes SET redeemed_count = redeemed_count + 1 "
    "WHERE id = :c AND redeemed_count < max_redemptions AND disabled_at IS NULL "
    "AND expires_at > now() RETURNING plan_id, features, grant_days"
)
_RECORD = text(
    "INSERT INTO code_redemptions (owner_id, code_id, entitlement_id) VALUES (:o, :c, :e)"
)
_UNIQUE = "code_redemptions_owner_id_code_id_key"


@dataclass(frozen=True, slots=True)
class Redeemed:
    plan_id: str | None
    features: tuple[str, ...]
    ends_at: datetime


RedeemRefusal = Literal["code_invalid", "already_redeemed"]


class AlreadyRedeemed(Exception):
    """The unique ``(owner_id, code_id)`` tripped: the caller's transaction must roll back."""


async def take(db: AsyncSession, ctx: UserContext, code_id: UUID) -> Redeemed | RedeemRefusal:
    """Steps 2 to 4 on a code whose row this transaction holds locked."""
    if (await db.execute(_MINE, {"o": ctx.user_id, "c": code_id})).scalar_one():
        return "already_redeemed"
    terms = (await db.execute(_TAKE, {"c": code_id})).one_or_none()
    if terms is None:
        return "code_invalid"
    added = await add_entitlement(
        db,
        ctx,
        source="code",
        plan_id=terms.plan_id,
        features=tuple(terms.features),
        days=terms.grant_days,
        code_id=code_id,
    )
    if added is None:  # only a trial can conflict
        raise RuntimeError("a code entitlement was not inserted")
    try:
        await db.execute(_RECORD, {"o": ctx.user_id, "c": code_id, "e": added.id})
    except IntegrityError as error:
        diag = getattr(error.orig, "diag", None)
        if getattr(diag, "constraint_name", None) == _UNIQUE:
            raise AlreadyRedeemed from None
        raise
    return Redeemed(added.plan_id, added.features, added.ends_at)


async def invite_code(db: AsyncSession, invite_id: UUID) -> UUID | None:
    """The code an invite carries (ADR 0033), its row locked; ``None`` for a plain invite."""
    return (await db.execute(_BY_INVITE, {"i": invite_id})).scalar_one_or_none()
