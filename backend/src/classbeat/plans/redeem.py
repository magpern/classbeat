"""Redeeming a code (ADR 0011 section 2): ``POST /api/account/redeem``.

One ``user_session``:

1. look the code up by its hash, ``FOR UPDATE`` (parallel redeems of one code queue here);
2. the user's own earlier redemption is ``already_redeemed`` (nothing changes);
3. the conditional count update: ``redeemed_count + 1`` only while uses are left, the code is not
   disabled and not expired;
4. the entitlement (``source = 'code'``, the code's plan and features, ``ends_at = now() +
   grant_days``) and the ``code_redemptions`` row.

Unknown, malformed, expired, disabled and used-up codes are all ``code_invalid``, so the answer
tells nothing about which. Should the unique ``(owner_id, code_id)`` still trip (it can't while
step 1 holds the row lock), the whole transaction rolls back, the count with it, and the answer is
``already_redeemed``. The code is never logged; only its hash reaches the database.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.plans.codes import hash_typed
from classbeat.plans.entitle import add_entitlement

_LOOKUP = text("SELECT id FROM redeem_codes WHERE code_hash = :h FOR UPDATE")
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


class _AlreadyRedeemed(Exception):
    pass


async def _redeem(db: AsyncSession, ctx: UserContext, digest: bytes) -> Redeemed | RedeemRefusal:
    code_id = (await db.execute(_LOOKUP, {"h": digest})).scalar_one_or_none()
    if code_id is None:
        return "code_invalid"
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
            raise _AlreadyRedeemed from None
        raise
    return Redeemed(added.plan_id, added.features, added.ends_at)


async def redeem_code(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, code: str
) -> Redeemed | RedeemRefusal:
    """Redeem ``code`` for the user, or say why not."""
    digest = hash_typed(code)
    if digest is None:
        return "code_invalid"
    try:
        async with user_session(factory, ctx) as db:
            return await _redeem(db, ctx, digest)
    except _AlreadyRedeemed:  # the session rolled back: count, entitlement and all
        return "already_redeemed"
