"""The tier an invite carries, granted when the invite is consumed (ADR 0033 section 4).

An invite issued from an invite request with a plan has one single-use redeem code tied to it
(``redeem_codes.invite_id``; nobody knows the code's text). Every path that consumes an invite and
creates the account (the password confirmation in ``signup_routes`` and both provider paths
through ``provider_account``) calls ``grant_invite_tier`` in the same transaction, right after the
user row exists. It redeems that code for the new account through the steps of the one redeem
path (``classbeat.plans.redeem_steps``, shared with ``POST /api/account/redeem``: the conditional
count, ``add_entitlement`` with ``source = 'code'``, the ``code_redemptions`` row), so there is no
second way to write an entitlement.

The two owned tables need ``app.user_id``: it is set to the new account for those statements and
cleared again (``''``, as ``system_session`` starts), both transaction-local. A refusal (the code
was disabled or has expired) is ``False``: the caller rolls the whole sign-up back as
``invite_invalid``, so an account never exists without the tier its invite promised. On any
exception the transaction (or the caller's savepoint) rolls back, and the setting with it.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.deps import invite_tier_context
from classbeat.db.session import SET_USER
from classbeat.plans.redeem_steps import Redeemed, invite_code, take


async def grant_invite_tier(db: AsyncSession, invite_id: UUID | None, user_id: UUID) -> bool:
    """Grant the tier of the consumed invite ``invite_id`` to ``user_id``; ``True`` when there is
    none (a plain invite, or no invite) or it was granted, ``False`` when its code is gone."""
    if invite_id is None:
        return True
    code_id = await invite_code(db, invite_id)
    if code_id is None:
        return True
    await db.execute(SET_USER, {"user_id": str(user_id)})
    result = await take(db, invite_tier_context(user_id), code_id)
    await db.execute(SET_USER, {"user_id": ""})
    return isinstance(result, Redeemed)
