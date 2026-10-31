"""The model route key of a job, chosen at creation (ADR 0011 section 6, AD5 #223; ADR 0012
section 5).

``with_route_key(admit)`` wraps a model kind's ``admit``: after the kind's own checks accept, the
job's ``route_key`` is the **effective plan's** ``plans.route_key`` (``classbeat.plans.resolve``,
read in the creating transaction, so a plan change applies to the next job only) and is added to
the fields ``admit`` stores with the input. The key is the server's alone: a browser sending
``route_key`` is refused 422 (``JobInput.SERVER_FIELDS``, checked by the jobs API before any
``admit``), whatever the kind's ``admit`` returns replaces nothing a browser sent, and the stored
input is what every attempt and the approved phase of a job that waited read, so the key never
changes while a job lives. A refused ``admit`` passes through untouched.
"""

from typing import Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs.kinds import Admit, Admitted, Refused
from classbeat.plans.resolve import effective_plan

ROUTE_KEY_FIELD: Final = "route_key"


async def route_key_for(db: AsyncSession, ctx: UserContext) -> str:
    """The route key of the user's effective plan (``free`` when no entitlement is active)."""
    return (await effective_plan(db, ctx)).route_key


def with_route_key(inner: Admit | None = None) -> Admit:
    """``inner`` (or an admit that accepts) followed by the plan's route key."""

    async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
        outcome = Admitted() if inner is None else await inner(db, ctx, parsed)
        if isinstance(outcome, Refused):
            return outcome
        fields = {**outcome.fields, ROUTE_KEY_FIELD: await route_key_for(db, ctx)}
        return Admitted(fields, outcome.after_insert)

    return admit
