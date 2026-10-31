"""Quota and concurrency at job creation and settle, on real parallel connections (ADR 0011
sections 3 and 4; QT1, #214; T85 of ``docs/security/threat-model.md``). Fakes only.

Every race runs ``classbeat.jobs.service`` (the jobs API's transactions: the owner lock, the
per-user limit, then the ledger) with the metered ledger, one connection per caller.

- 20 parallel creates against 3 classes a month (and a job limit of 20) give exactly 3
  ``reserved`` rows and 17 ``quota`` refusals that created no class, job or event.
- With the default concurrency (free: one job at a time) the month still holds exactly 3: every
  other answer is ``busy`` or ``quota``, and a released credit frees its slot at once.
- Parallel creates never pass the plan's ``concurrent_jobs``, and ``CLASSBEAT_JOBS_PER_USER``
  bounds every plan.
- Each terminal state settles once through the metered ledger: ``completed`` commits, the rest
  release; a run already going is never stopped by the cost cap.
"""

import asyncio
import secrets
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.jobs import service
from classbeat.jobs.credits import settle_one
from classbeat.jobs.user import JobLimitPolicy, NewJob
from classbeat.quota import Bounds, MeteredLedger, PlanJobLimitPolicy
from ctx import make_context
from jobtools import digest, new_user
from quotatools import Factory, N, admin, count, factory, ledger_states, test_plan

__all__ = ["admin", "factory", "test_plan"]  # fixtures

LEDGER = MeteredLedger(Bounds())


def new(title: str = "Tuesday climb") -> NewJob:
    return NewJob(
        kind="test_kind",
        input="{}",
        class_id=None,
        title=title,
        connection_ids={},
        idempotency_key=secrets.token_urlsafe(18),
        request_hash=digest(secrets.token_hex(8)),
    )


async def burst(
    factory: Factory, owner: UUID, limit: int | JobLimitPolicy, n: int = N
) -> list[service.CreateResult]:
    ctx = make_context(owner)
    return list(
        await asyncio.gather(
            *(service.create(factory, ctx, new(), limit=limit, ledger=LEDGER) for _ in range(n))
        )
    )


def owned(conn: Connection, owner: UUID) -> tuple[int, int, int]:
    """The owner's classes, jobs and job events."""
    return (
        count(conn, "SELECT count(*) FROM classes WHERE owner_id = :o", o=owner),
        count(conn, "SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner),
        count(conn, "SELECT count(*) FROM job_events WHERE owner_id = :o", o=owner),
    )


async def finish(conn: Connection, factory: Factory, owner: UUID, state: str) -> list[str | None]:
    """End the owner's open jobs in ``state`` (as the worker would) and settle each."""
    ids = conn.execute(
        text(
            "UPDATE jobs SET state = :s, finished_at = now(), leased_by = NULL, "
            "lease_expires_at = NULL, error_category = CASE WHEN :s = 'failed' "
            "THEN 'upstream_unavailable' END WHERE owner_id = :o "
            "AND state IN ('queued', 'running') RETURNING id"
        ),
        {"s": state, "o": owner},
    ).scalars()
    return [await settle_one(factory, LEDGER, owner, job_id) for job_id in list(ids)]


async def test_twenty_parallel_creates_against_three_classes_reserve_three(
    admin: Connection, factory: Factory
) -> None:
    owner = new_user(admin)  # free: 3 classes a month
    results = await burst(factory, owner, limit=N)
    created = [r for r in results if r.created]
    refused = [r for r in results if not r.created]
    assert len(created) == 3
    assert {r.reason for r in refused} == {"quota"}
    assert len(refused) == N - 3
    assert all(r.job is None and r.counts["quota"] == "classes" for r in refused)
    assert {(r.counts["limit"], r.counts["used"], r.counts["plan"]) for r in refused} == {
        (3, 3, "free")
    }
    assert ledger_states(admin, owner) == ["reserved"] * 3
    assert owned(admin, owner) == (3, 3, 3)  # the refusals created nothing


async def test_the_default_concurrency_still_holds_exactly_three(
    admin: Connection, factory: Factory
) -> None:
    owner = new_user(admin)  # free: one job at a time, under the default bound of 2
    answers: list[str | None] = []
    for _ in range(5):
        results = await burst(factory, owner, limit=PlanJobLimitPolicy())
        answers += [None if r.created else r.reason for r in results]
        assert len([r for r in results if r.created]) <= 1
        await finish(admin, factory, owner, "completed")
    assert answers.count(None) == 3
    assert set(answers) - {None} <= {"busy", "quota"}
    assert answers[-N:] == ["quota"] * N  # the month is used up, whatever is running
    assert ledger_states(admin, owner) == ["committed"] * 3
    assert owned(admin, owner)[1] == 3  # held exactly 3, and no other job or credit exists
    busy = [r for r in await burst(factory, new_user(admin), PlanJobLimitPolicy()) if r.reason]
    assert {(r.reason, r.counts.get("limit")) for r in busy} == {("busy", 1)}


async def test_a_released_credit_frees_its_slot_at_once(
    admin: Connection, factory: Factory
) -> None:
    owner = new_user(admin)
    assert len([r for r in await burst(factory, owner, N) if r.created]) == 3
    assert await finish(admin, factory, owner, "failed") == ["released"] * 3
    assert len([r for r in await burst(factory, owner, N) if r.created]) == 3
    assert ledger_states(admin, owner) == ["released"] * 3 + ["reserved"] * 3


# The plan's own limit, the environment's bound over a larger plan, and the plans table's real
# maximum (``concurrent_jobs`` is at most 16) under an environment bound that allows it.
@pytest.mark.parametrize(("plan_jobs", "bound", "expected"), [(3, 16, 3), (16, 2, 2), (16, 16, 16)])
async def test_parallel_creates_never_pass_the_plans_concurrent_jobs(
    admin: Connection,
    factory: Factory,
    test_plan: Callable[..., None],
    plan_jobs: int,
    bound: int,
    expected: int,
) -> None:
    owner = new_user(admin)
    test_plan(owner, classes_per_month=100, concurrent_jobs=plan_jobs)
    results = await burst(factory, owner, PlanJobLimitPolicy(bound))
    assert len([r for r in results if r.created]) == expected
    refused = {(r.reason, r.counts.get("limit")) for r in results if not r.created}
    assert refused == {("busy", expected)}
    assert ledger_states(admin, owner) == ["reserved"] * expected
    assert owned(admin, owner) == (expected, expected, expected)


@pytest.mark.parametrize(
    ("state", "columns", "expected", "reason"),
    [
        ("completed", "", "committed", None),
        ("failed", ", error_category = 'upstream_unavailable'", "released", "failed"),
        ("cancelled", "", "released", "cancelled"),
        ("expired", "", "released", "expired"),
    ],
)
async def test_each_terminal_state_settles_once_through_the_ledger(
    admin: Connection,
    factory: Factory,
    state: str,
    columns: str,
    expected: str,
    reason: str | None,
) -> None:
    owner = new_user(admin)
    (done,) = [r for r in await burst(factory, owner, N, n=1) if r.job]
    assert done.job is not None
    admin.execute(
        text(f"UPDATE jobs SET state = :s, finished_at = now(){columns} WHERE id = :j"),  # noqa: S608
        {"s": state, "j": done.job.id},
    )
    settled = await asyncio.gather(
        *(settle_one(factory, LEDGER, owner, done.job.id) for _ in range(N))
    )
    assert sorted(s or "" for s in settled) == [""] * (N - 1) + [expected]
    entry: Any = admin.execute(
        text("SELECT state, reason, settled_at FROM credit_ledger WHERE job_id = :j"),
        {"j": done.job.id},
    ).one()
    credit_state = admin.execute(
        text("SELECT credit_state FROM jobs WHERE id = :j"), {"j": done.job.id}
    ).scalar_one()
    assert (entry.state, entry.reason, credit_state) == (expected, reason, expected)
    assert entry.settled_at is not None


async def test_the_cost_cap_never_stops_a_run_already_going(
    admin: Connection, factory: Factory
) -> None:
    from test_plans_routes import plant_run

    owner = new_user(admin)
    (going,) = [r.job for r in await burst(factory, owner, N, n=1) if r.job]
    assert going is not None
    admin.execute(
        text(
            "UPDATE jobs SET state = 'running', attempt = 1, leased_by = 'other-worker', "
            "lease_expires_at = now() + interval '5 minutes' WHERE id = :j"
        ),
        {"j": going.id},
    )
    plant_run(admin, owner, 10_000_000)  # the cap is reached while it runs
    later = await burst(factory, owner, N, n=1)
    assert [(r.reason, r.counts["quota"]) for r in later] == [("quota", "cost")]
    assert await finish(admin, factory, owner, "completed") == ["committed"]
    assert ledger_states(admin, owner) == ["committed"]


async def test_a_pre_m5_job_settles_as_a_no_op(admin: Connection, factory: Factory) -> None:
    from jobtools import new_job

    owner = new_user(admin)
    job_id = new_job(admin, owner, state="completed", finished_at="2026-10-02")
    assert await settle_one(factory, LEDGER, owner, job_id) == "committed"
    assert ledger_states(admin, owner) == []
