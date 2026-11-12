"""Redeeming a code (ADR 0011 section 2): ``POST /api/account/redeem``.

One ``user_session``:

1. look the code up by its hash, ``FOR UPDATE`` (parallel redeems of one code queue here);
2. to 4. ``classbeat.plans.redeem_steps.take``: the user's own earlier redemption is
   ``already_redeemed`` (nothing changes); the conditional count update; the entitlement
   (``source = 'code'``, the code's plan and features, ``ends_at = now() + grant_days``) and the
   ``code_redemptions`` row.

Unknown, malformed, expired, disabled and used-up codes are all ``code_invalid``, so the answer
tells nothing about which. Should the unique ``(owner_id, code_id)`` still trip (it can't while
step 1 holds the row lock), the whole transaction rolls back, the count with it, and the answer is
``already_redeemed``. The code is never logged; only its hash reaches the database.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session
from classbeat.plans.codes import hash_typed
from classbeat.plans.redeem_steps import AlreadyRedeemed, Redeemed, RedeemRefusal, take

__all__ = ["RedeemRefusal", "Redeemed", "redeem_code"]

_LOOKUP = text("SELECT id FROM redeem_codes WHERE code_hash = :h FOR UPDATE")


async def _redeem(db: AsyncSession, ctx: UserContext, digest: bytes) -> Redeemed | RedeemRefusal:
    code_id = (await db.execute(_LOOKUP, {"h": digest})).scalar_one_or_none()
    if code_id is None:
        return "code_invalid"
    return await take(db, ctx, code_id)


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
    except AlreadyRedeemed:  # the session rolled back: count, entitlement and all
        return "already_redeemed"
