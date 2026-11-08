"""The caps on Save and Update per user (issue #577).

Neither kind takes a credit or a model call, so before these the only bound on writes to the
user's real MOWL account was the per-user job limit: a script with a stolen session could keep
saving programs two at a time, all day. ``check_write_caps`` runs first in both kinds' ``admit``,
in the jobs API's create transaction under the owner lock (``classbeat.jobs.user.create``), so
parallel requests are counted one after another and cannot overshoot. A replay of a request that
was already created never reaches it (the idempotency key is checked before ``admit``).

What counts is every ``save_workout`` and ``update_workout`` job the user created in the last
hour and in the last 24 hours (rolling, by the database's clock), whatever became of it: a job
that failed may still have reached MOWL. Above the hour's or the day's cap (the user's plan value
under ``CLASSBEAT_SAVES_PER_HOUR`` or ``CLASSBEAT_SAVES_PER_DAY``, which a plan can only tighten:
``classbeat.quota.save_caps_policy``, #806) the request is a 409 ``quota`` ``saves`` with
``limit``, ``used`` and ``window`` (``hour`` or ``day``), and nothing is created.
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context
from classbeat.jobs.credits import QuotaRefused
from classbeat.plans.resolve import effective_plan
from classbeat.quota import bounds as quota_bounds
from classbeat.quota.save_caps_policy import effective_save_caps

WRITE_KINDS: Final = ("save_workout", "update_workout")

_COUNTS = text(
    "SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS hour, "
    "count(*) AS day FROM jobs WHERE owner_id = :o AND kind = ANY(:kinds) "
    "AND created_at > now() - interval '1 day'"
)


async def check_write_caps(db: AsyncSession, ctx: UserContext) -> None:
    """Raise ``QuotaRefused`` ``saves`` when the user already created as many Save and Update
    jobs as the hour's or the day's cap allows."""
    ctx = require_context(ctx)
    bounds = quota_bounds.current()
    plan = await effective_plan(db, ctx)
    caps = effective_save_caps(
        plan.saves_per_hour, plan.saves_per_day, bounds.saves_per_hour, bounds.saves_per_day
    )
    row = (await db.execute(_COUNTS, {"o": ctx.user_id, "kinds": list(WRITE_KINDS)})).one()
    if row.hour >= caps.per_hour:
        raise QuotaRefused("saves", limit=caps.per_hour, used=row.hour, window="hour")
    if row.day >= caps.per_day:
        raise QuotaRefused("saves", limit=caps.per_day, used=row.day, window="day")
