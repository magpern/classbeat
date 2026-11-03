"""The model route key of a job, chosen at creation (ADR 0011 section 6, AD5 #223; ADR 0012
section 5).

``with_plan(admit)`` wraps a model kind's ``admit``: after the kind's own checks accept, the
job's ``route_key`` is the **effective plan's** ``plans.route_key`` (``classbeat.plans.resolve``,
read in the creating transaction, so a plan change applies to the next job only) and is added to
the fields ``admit`` stores with the input. The key is the server's alone: a browser sending
``route_key`` is refused 422 (``JobInput.SERVER_FIELDS``, checked by the jobs API before any
``admit``), whatever the kind's ``admit`` returns replaces nothing a browser sent, and the stored
input is what every attempt and the approved phase of a job that waited read, so the key never
changes while a job lives. A refused ``admit`` passes through untouched.

Since #496 the same wrapper also gates the brief: the effective plan is read **once**, before
the kind's own checks, and the brief the job designs for (the input's, or a
revise kind's stored class brief) is checked against its class length cap and feature gates
(``classbeat.quota.gates``: 409 ``quota`` ``length`` or ``feature``, nothing created); then the
kind's ``admit``; then the plan's ``route_key`` and ``plan_gates`` (``classbeat.plan_gates``) join
the stored input as server fields, so the worker enforces on every draft exactly what the user was
admitted under. The plan and the bounds are read in the creating transaction, never cached, so an
admin's change applies to the next request.
"""

from typing import Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs.kinds import Admit, Admitted, Refused
from classbeat.plan_gates import FIELD as GATES_FIELD
from classbeat.plans.resolve import effective_plan
from classbeat.quota import bounds as quota_bounds
from classbeat.quota.gates import brief_of, check_brief, gates_for

ROUTE_KEY_FIELD: Final = "route_key"


def with_plan(inner: Admit | None = None) -> Admit:
    """The plan's gates on the brief, then ``inner``, then the route key and the gates."""

    async def admit(db: AsyncSession, ctx: UserContext, parsed: BaseModel, /) -> Admitted | Refused:
        plan = await effective_plan(db, ctx)
        gates = gates_for(plan, quota_bounds.current())
        check_brief(gates, await brief_of(db, ctx, parsed), plan.id)  # QuotaRefused: 409 quota
        outcome = Admitted() if inner is None else await inner(db, ctx, parsed)
        if isinstance(outcome, Refused):
            return outcome
        fields = {
            **outcome.fields,
            ROUTE_KEY_FIELD: plan.route_key,
            GATES_FIELD: gates.model_dump(mode="json"),
        }
        return Admitted(fields, outcome.after_insert)

    return admit
