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

``with_plan_run(run)`` wraps the same four kinds' ``run`` and **fails closed** for an input that
carries no ``plan_gates`` (#496, second PR): a job queued before the gates existed, or a song pick
created then whose approved phase runs later (it may wait ``CLASSBEAT_APPROVAL_TTL_HOURS``). Before
the kind calls anything, the worker reads the user's **current** effective plan in a fenced
transaction, refuses the attempt ``invalid_input`` when the brief the job designs for (the same
``brief_of``) is over its cap or uses a gate it does not include, and otherwise runs the kind with
those gates, so every draft the attempt submits is checked against them. Every attempt and the
approved phase read the plan again (nothing is stored), so a gate-less job never runs under more
than the plan the user has when it runs. An input that carries gates is left as it is: it runs
under the gates it was admitted with (ADR 0011, 2026-11-03 amendment).
"""

from collections.abc import Awaitable, Callable
from typing import Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext
from classbeat.jobs.credits import QuotaRefused
from classbeat.jobs.errors import JobFailure
from classbeat.jobs.inputs import ModelJobInput
from classbeat.jobs.kinds import Admit, Admitted, Outcome, Refused
from classbeat.jobs.run import JobRun
from classbeat.plan_gates import FIELD as GATES_FIELD
from classbeat.plans.resolve import effective_plan
from classbeat.quota import bounds as quota_bounds
from classbeat.quota.gates import brief_of, check_brief, current_gates, gates_for

ROUTE_KEY_FIELD: Final = "route_key"

Run = Callable[[JobRun], Awaitable[Outcome]]


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


def with_plan_run(inner: Run) -> Run:
    """``inner`` with the current plan's gates when the stored input carries none."""

    async def run(job_run: JobRun) -> Outcome:
        given = job_run.input
        if not isinstance(given, ModelJobInput):
            raise JobFailure("internal")  # only the model kinds are wrapped
        if given.plan_gates is None:
            refused = False
            async with job_run.fenced() as fs:
                plan, gates = await current_gates(fs.db, job_run.ctx)
                try:
                    check_brief(gates, await brief_of(fs.db, job_run.ctx, given), plan.id)
                except QuotaRefused:
                    refused = True
            if refused:
                raise JobFailure("invalid_input")  # nothing was called; not retried
            job_run.input = given.model_copy(update={GATES_FIELD: gates})
        return await inner(job_run)

    return run
