"""``classbeat.quota.MeteredLedger`` against the real tables, policies and trigger (ADR 0011
section 3; QT1, #214). Fakes only; every call runs in a ``user_session`` as the app role.

- ``reserve`` serialises on the period's ``quota_periods`` row: 20 reservations on 20
  connections at once, against a limit of 3 and without the owner lock, hold exactly 3.
- Held is ``committed`` plus ``reserved`` with a job; a released, refunded or orphaned reserved
  row and last month's rows don't count. A deleted class keeps its committed credit.
- The cost cap and the environment's bounds; ``QuotaRefused`` carries the numbers.
- ``allows``, ``commit`` and ``release``, against the trigger of ``0008`` and of #276.
"""

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.db import user_session
from classbeat.jobs.credits import QuotaRefused
from classbeat.quota import Bounds, MeteredLedger
from classbeat.quota.bounds import next_month
from ctx import make_context
from jobtools import digest, new_job, new_user
from quotatools import Factory, Meeting, N, admin, count, factory, guard, ledger_states, test_plan
from test_plans_routes import plant_run
from test_plans_schema import ledger, row

__all__ = ["admin", "factory", "guard", "test_plan"]  # fixtures

LEDGER = MeteredLedger(Bounds())
FAILED: dict[str, Any] = {"error_category": "upstream_unavailable"}
RUNNING: dict[str, Any] = {
    "attempt": 1,
    "leased_by": "other-worker",
    "lease_expires_at": "2099-01-01",
}
WAITING: dict[str, Any] = {"approval_hash": digest("list"), "expires_at": "2099-01-01"}
ENDED = "2026-10-02"


def this_period(conn: Connection) -> date:
    return conn.execute(
        text("SELECT CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date)")
    ).scalar_one()


async def reserve(factory: Factory, owner: UUID, job_id: UUID, **bounds: int) -> None:
    ctx = make_context(owner)
    async with user_session(factory, ctx) as db:
        await MeteredLedger(Bounds(**bounds)).reserve(db, ctx, job_id)


async def refusal(factory: Factory, owner: UUID, job_id: UUID, **bounds: int) -> QuotaRefused:
    with pytest.raises(QuotaRefused) as caught:
        await reserve(factory, owner, job_id, **bounds)
    return caught.value


# --- reserve under concurrency ----------------------------------------------------------------


async def test_twenty_parallel_reservations_against_three_hold_exactly_three(
    admin: Connection, factory: Factory
) -> None:
    """No owner lock here: only the period row's ``FOR UPDATE`` keeps the count honest. Each
    caller stays in its transaction after reserving until all have reserved (or a moment
    passes), so without the row lock all 20 would count 0 held and insert."""
    owner = new_user(admin)
    jobs = [new_job(admin, owner) for _ in range(N)]
    ctx = make_context(owner)
    meet = Meeting(N)

    async def one(job_id: UUID) -> str:
        try:
            async with user_session(factory, ctx) as db:
                await LEDGER.reserve(db, ctx, job_id)
                await meet()
        except QuotaRefused as refused:
            return refused.reason
        return "reserved"

    results = await asyncio.gather(*(one(j) for j in jobs))
    assert sorted(results) == ["classes"] * (N - 3) + ["reserved"] * 3
    assert ledger_states(admin, owner) == ["reserved"] * 3
    assert count(admin, "SELECT count(*) FROM quota_periods WHERE owner_id = :o", o=owner) == 1


async def test_two_users_reserve_in_parallel_without_sharing_a_count(
    admin: Connection, factory: Factory
) -> None:
    alice, bob = new_user(admin), new_user(admin)
    calls = [(u, new_job(admin, u)) for u in (alice, bob) for _ in range(N // 2)]
    results = await asyncio.gather(
        *(reserve(factory, u, j) for u, j in calls), return_exceptions=True
    )
    assert len([r for r in results if r is None]) == 6
    assert ledger_states(admin, alice) == ledger_states(admin, bob) == ["reserved"] * 3


# --- what counts as held ----------------------------------------------------------------------


async def test_held_is_committed_and_reserved_with_a_job_of_this_period(
    admin: Connection, factory: Factory
) -> None:
    owner = new_user(admin)
    ledger(admin, owner, new_job(admin, owner), state="committed", settled_at="2026-10-02")
    for state, reason in (("released", "failed"), ("refunded", "admin_refund")):
        ledger(admin, owner, new_job(admin, owner), state=state, reason=reason)
    ledger(admin, owner, None)  # a reservation whose job was deleted: nothing was delivered
    last_month = admin.execute(
        text(
            "SELECT CAST(date_trunc('month', now() AT TIME ZONE 'UTC') - interval '1 month' "
            "AS date)"
        )
    ).scalar_one()
    for _ in range(3):
        admin.execute(
            text(
                "INSERT INTO credit_ledger (owner_id, job_id, period, plan_id, state) "
                "VALUES (:o, :j, :p, 'free', 'committed')"
            ),
            {"o": owner, "j": new_job(admin, owner), "p": last_month},
        )
    await reserve(factory, owner, new_job(admin, owner))  # held 1 of 3
    await reserve(factory, owner, new_job(admin, owner))  # held 2 of 3
    refused = await refusal(factory, owner, new_job(admin, owner))
    assert (refused.reason, refused.limit, refused.used) == ("classes", 3, 3)


async def test_a_deleted_class_keeps_its_committed_credit_and_frees_a_reserved_one(
    admin: Connection, factory: Factory
) -> None:
    """Threats: T87."""
    owner = new_user(admin)
    delivered = new_job(admin, owner, state="completed", finished_at=ENDED)
    kept = ledger(admin, owner, delivered, state="committed", settled_at="2026-10-02")
    running = new_job(admin, owner, state="running", **RUNNING)
    freed = ledger(admin, owner, running)
    ledger(admin, owner, new_job(admin, owner), state="committed", settled_at="2026-10-02")
    for job_id in (delivered, running):  # the class goes, its job with it (the FK cascade)
        admin.execute(
            text("DELETE FROM classes WHERE id = (SELECT class_id FROM jobs WHERE id = :j)"),
            {"j": job_id},
        )
    assert (row(admin, kept).state, row(admin, kept).job_id) == ("committed", None)
    assert (row(admin, freed).state, row(admin, freed).job_id) == ("reserved", None)
    await reserve(factory, owner, new_job(admin, owner))  # held: 2 committed, the freed one not
    refused = await refusal(factory, owner, new_job(admin, owner))
    assert (refused.limit, refused.used) == (3, 3)


async def test_the_refusal_names_the_plan_and_the_end_of_the_period(
    admin: Connection, factory: Factory, test_plan: Callable[..., None]
) -> None:
    owner = new_user(admin)
    test_plan(owner, classes_per_month=1)
    await reserve(factory, owner, new_job(admin, owner))
    refused = await refusal(factory, owner, new_job(admin, owner))
    first = this_period(admin)
    assert (refused.reason, refused.limit, refused.used, refused.plan) == (
        "classes",
        1,
        1,
        "quota_test",
    )
    assert refused.period_end == next_month(first)
    assert refused.detail() == {
        "quota": "classes",
        "limit": 1,
        "used": 1,
        "period_end": next_month(first).isoformat(),
        "plan": "quota_test",
    }


def test_the_period_end_is_the_first_of_the_next_month() -> None:
    assert next_month(date(2026, 10, 1)) == date(2026, 11, 1)
    assert next_month(date(2026, 12, 1)) == date(2027, 1, 1)


# --- the cost cap and the bounds --------------------------------------------------------------


async def test_the_cost_cap_refuses_a_reservation_and_counts_this_period_only(
    admin: Connection, factory: Factory
) -> None:
    """Threats: T86."""
    owner = new_user(admin)
    plant_run(admin, owner, 10_000_000, "now() - interval '40 days'")  # last month: not counted
    plant_run(admin, owner, 9_999_999)
    await reserve(factory, owner, new_job(admin, owner))
    plant_run(admin, owner, 1)  # at free's cap of $10
    job_id = new_job(admin, owner)
    refused = await refusal(factory, owner, job_id)
    assert (refused.reason, refused.limit, refused.used, refused.plan) == (
        "cost",
        10_000_000,
        10_000_000,
        "free",
    )
    assert "limit" not in refused.detail()
    assert "used" not in refused.detail()
    assert ledger_states(admin, owner) == ["reserved"]


async def test_the_environment_bounds_every_plan(
    admin: Connection, factory: Factory, test_plan: Callable[..., None]
) -> None:
    owner = new_user(admin)
    test_plan(owner, classes_per_month=10_000, monthly_cost_micros=10_000_000_000)
    await reserve(factory, owner, new_job(admin, owner), classes_per_month_max=2)
    await reserve(factory, owner, new_job(admin, owner), classes_per_month_max=2)
    refused = await refusal(factory, owner, new_job(admin, owner), classes_per_month_max=2)
    assert (refused.reason, refused.limit) == ("classes", 2)
    plant_run(admin, owner, 500)
    refused = await refusal(factory, owner, new_job(admin, owner), monthly_cost_micros_max=500)
    assert (refused.reason, refused.limit) == ("cost", 500)
    await reserve(factory, owner, new_job(admin, owner))  # the default bounds: 200 and $300


# --- allows, commit, release ------------------------------------------------------------------


async def allows(factory: Factory, owner: UUID, job_id: UUID) -> bool:
    ctx = make_context(owner)
    async with user_session(factory, ctx) as db:
        return await LEDGER.allows(db, ctx, job_id)


async def test_allows_passes_a_held_or_pre_m5_credit_and_refuses_a_settled_one(
    admin: Connection, factory: Factory
) -> None:
    owner = new_user(admin)
    held = new_job(admin, owner, state="waiting_for_approval", **WAITING)
    ledger(admin, owner, held)
    assert await allows(factory, owner, held)
    assert await allows(
        factory, owner, new_job(admin, owner, state="waiting_for_approval", **WAITING)
    )
    for state, reason in (("committed", None), ("released", "expired")):
        settled = new_job(admin, owner, state="waiting_for_approval", **WAITING)
        ledger(admin, owner, settled, state=state, reason=reason)
        assert not await allows(factory, owner, settled)


async def test_allows_refuses_at_the_cost_cap(admin: Connection, factory: Factory) -> None:
    owner = new_user(admin)
    held = new_job(admin, owner, state="waiting_for_approval", **WAITING)
    ledger(admin, owner, held)
    plant_run(admin, owner, 10_000_000)
    with pytest.raises(QuotaRefused) as caught:
        await allows(factory, owner, held)
    assert caught.value.reason == "cost"


@pytest.mark.parametrize(
    ("job_state", "settled", "reason"),
    [
        ("completed", "committed", None),
        ("failed", "released", "failed"),
        ("cancelled", "released", "cancelled"),
        ("expired", "released", "expired"),
    ],
)
async def test_commit_and_release_write_what_both_triggers_allow(
    admin: Connection,
    factory: Factory,
    guard: str,
    job_state: str,
    settled: str,
    reason: str | None,
) -> None:
    owner = new_user(admin)
    extra = FAILED if job_state == "failed" else {}
    job_id = new_job(admin, owner, state=job_state, finished_at=ENDED, **extra)
    entry = ledger(admin, owner, job_id)
    ctx = make_context(owner)
    async with user_session(factory, ctx) as db:
        settle = LEDGER.commit if settled == "committed" else LEDGER.release
        await settle(db, ctx, job_id)
        stamped = (
            await db.execute(
                text("SELECT settled_at = now() FROM credit_ledger WHERE id = :i"), {"i": entry}
            )
        ).scalar_one()
    assert stamped is True
    got = row(admin, entry)
    assert (got.state, got.reason, got.refunded_by) == (settled, reason, None)


async def test_settling_without_a_ledger_row_or_twice_is_a_no_op(
    admin: Connection, factory: Factory, guard: str
) -> None:
    owner = new_user(admin)
    pre_m5 = new_job(admin, owner, state="completed", finished_at="2026-10-02")
    done = new_job(admin, owner, state="failed", finished_at=ENDED, **FAILED)
    entry = ledger(admin, owner, done)
    ctx = make_context(owner)
    for _ in range(2):
        async with user_session(factory, ctx) as db:
            await LEDGER.commit(db, ctx, pre_m5)
            await LEDGER.release(db, ctx, done)
            await LEDGER.commit(db, ctx, done)  # settled already: the row is not reserved
    assert (row(admin, entry).state, row(admin, entry).reason) == ("released", "failed")
    assert ledger_states(admin, owner) == ["released"]


async def test_release_never_settles_a_job_that_has_not_ended(
    admin: Connection, factory: Factory
) -> None:
    owner = new_user(admin)
    running = new_job(admin, owner, state="running", **RUNNING)
    entry = ledger(admin, owner, running)
    ctx = make_context(owner)
    async with user_session(factory, ctx) as db:
        await LEDGER.release(db, ctx, running)
    assert row(admin, entry).state == "reserved"
