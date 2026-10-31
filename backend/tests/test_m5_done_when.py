"""M5 done-when through the HTTP API (#228, ADR 0011 section 10): "limits hold under concurrency
and refunds work", for what the other suites check one layer down (service calls, SQL). Fakes
only, no network.

- Parallel ``POST /api/jobs`` from one user at the plan's class limit admit exactly the limit;
  the rest are 409 ``quota`` with ``quota``, ``limit``, ``used``, ``period_end`` and ``plan`` (a
  cost refusal carries no money). At the plan's job limit the rest are 429 ``busy`` with it.
  Refused creates leave no class, job or ledger row.
- A cancelled job gives its credit back once (a repeated cancel changes nothing) and frees a
  slot; an admin refund of a committed credit frees one too, is audited once and settles once.
- Two users never affect each other, each racing at once.
- A plan limit changed through ``PATCH /api/admin/plans/{id}`` applies to the next reservation.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from adminviews import audit, new_job, new_ledger, stepped_admin
from authtools import Env, running_env
from classbeat.jobs.credits import settle_one
from classbeat.quota import Bounds, MeteredLedger
from classbeat.quota.bounds import next_month
from jobapi import Browser
from quotatools import admin, count, factory, test_plan

__all__ = ["admin", "factory", "test_plan"]  # fixtures

N = 12
LIMIT = 3
PLAN = "quota_test"
CAP = 10_000_000  # free's monthly cost
LEDGER = MeteredLedger(Bounds())


@pytest.fixture
def users_plan() -> None:
    """These users start on ``free`` (no grant)."""
    return None


def reset_plans(conn: Connection) -> None:
    conn.execute(
        text(
            "UPDATE plans SET classes_per_month = v.c, revisions_per_class = v.r, "
            "concurrent_jobs = v.j, monthly_cost_micros = v.m, updated_by = NULL "
            "FROM (VALUES ('free', 3, 3, 1, 10000000), ('instructor', 30, 10, 2, 60000000), "
            "('pro', 100, 25, 3, 200000000)) AS v (id, c, r, j, m) WHERE plans.id = v.id"
        )
    )


@pytest.fixture(autouse=True)
def seeded_plans(superuser: Connection) -> Any:
    """The plans as migration 0007 seeded them, before and after (a test edits ``free``)."""
    reset_plans(superuser)
    yield
    reset_plans(superuser)


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    """The app with the admin area and a job limit (16) that leaves the plans' own in charge."""
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        jobs_per_user=16,
    ) as running:
        yield running


def period_end(conn: Connection) -> str:
    first: date = conn.execute(
        text("SELECT CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date)")
    ).scalar_one()
    return next_month(first).isoformat()


def held(conn: Connection, owner: UUID) -> int:
    return count(
        conn,
        "SELECT count(*) FROM credit_ledger WHERE owner_id = :o AND (state = 'committed' "
        "OR (state = 'reserved' AND job_id IS NOT NULL))",
        o=owner,
    )


def rows_of(conn: Connection, owner: UUID) -> tuple[int, int, int]:
    """(classes, jobs, ledger rows) of ``owner``: what a refused create must not add to."""
    return (
        count(conn, "SELECT count(*) FROM classes WHERE owner_id = :o", o=owner),
        count(conn, "SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner),
        count(conn, "SELECT count(*) FROM credit_ledger WHERE owner_id = :o", o=owner),
    )


async def finish(env: Env, conn: Connection, owner: UUID, state: str) -> list[UUID]:
    """The worker ends the owner's queued jobs in ``state`` and the settle pass settles them."""
    ids = [
        r.id
        for r in conn.execute(
            text(
                "UPDATE jobs SET state = :s, finished_at = now() WHERE owner_id = :o "
                "AND state IN ('queued', 'cancelled') RETURNING id"
            ),
            {"s": state, "o": owner},
        )
    ]
    for job_id in ids:
        await settle_one(env.factory, LEDGER, owner, job_id)
    return ids


def quota_body(plan: str, limit: int, used: int, period: str) -> dict[str, Any]:
    return {
        "detail": {
            "reason": "quota",
            "quota": "classes",
            "limit": limit,
            "used": used,
            "period_end": period,
            "plan": plan,
        }
    }


async def burst(browser: Browser, n: int = N) -> list[Any]:
    return list(await asyncio.gather(*(browser.create() for _ in range(n))))


def statuses(responses: list[Any]) -> list[int]:
    return sorted(r.status_code for r in responses)


async def test_parallel_creates_at_the_class_limit_admit_exactly_the_limit(
    admin: Connection, browsers: Any, test_plan: Callable[..., None]
) -> None:
    alice: Browser = await browsers()
    test_plan(alice.user_id, classes_per_month=LIMIT, concurrent_jobs=16)
    responses = await burst(alice)
    assert statuses(responses) == [201] * LIMIT + [409] * (N - LIMIT), [r.text for r in responses]
    for refused in (r for r in responses if r.status_code == 409):
        assert refused.json() == quota_body(PLAN, LIMIT, LIMIT, period_end(admin))
    assert rows_of(admin, alice.user_id) == (LIMIT, LIMIT, LIMIT)  # the 9 left nothing behind
    assert held(admin, alice.user_id) == LIMIT
    again = await alice.create()  # a later create is refused too
    assert (again.status_code, again.json()) == (
        409, quota_body(PLAN, LIMIT, LIMIT, period_end(admin)),
    )  # fmt: skip


async def test_parallel_creates_at_the_job_limit_admit_the_limit_and_the_rest_are_busy(
    admin: Connection, browsers: Any, test_plan: Callable[..., None]
) -> None:
    alice: Browser = await browsers()
    test_plan(alice.user_id, classes_per_month=50, concurrent_jobs=4)
    responses = await burst(alice)
    assert statuses(responses) == [201] * 4 + [429] * (N - 4), [r.text for r in responses]
    for busy in (r for r in responses if r.status_code == 429):
        assert busy.json() == {"detail": {"reason": "busy", "limit": 4}}
    assert rows_of(admin, alice.user_id) == (4, 4, 4)


async def test_a_free_user_racing_creates_starts_one_at_a_time_and_never_past_the_month(
    admin: Connection, browsers: Any, env: Env
) -> None:
    alice: Browser = await browsers()
    first = await burst(alice)
    assert statuses(first) == [201] + [429] * (N - 1)  # free: one job at a time
    assert {r.json()["detail"]["limit"] for r in first if r.status_code == 429} == {1}
    await finish(env, admin, alice.user_id, "completed")
    for _ in range(LIMIT - 1):  # each job ends, the next race admits exactly one
        assert statuses(await burst(alice)) == [201] + [429] * (N - 1)
        await finish(env, admin, alice.user_id, "completed")
    assert held(admin, alice.user_id) == LIMIT
    over = await burst(alice)
    assert statuses(over) == [409] * N
    assert {r.json()["detail"]["quota"] for r in over} == {"classes"}


async def test_a_cost_refusal_carries_no_money(admin: Connection, browsers: Any) -> None:
    alice: Browser = await browsers()
    new_run_cost = CAP
    admin.execute(
        text(
            "INSERT INTO agent_runs (owner_id, stage, attempt, provider, model, effort, "
            "prompt_version, input_tokens, output_tokens, cost_micros, job_kind, price_version, "
            "started_at) VALUES (:o, 'design', 1, 'fake', 'fake-model', 'medium', 'v1', 1, 1, :c, "
            "'design_draft', '2026-10-29', now())"
        ),
        {"o": alice.user_id, "c": new_run_cost},
    )
    refused = await burst(alice, 4)
    assert statuses(refused) == [409] * 4
    for response in refused:
        assert response.json() == {
            "detail": {
                "reason": "quota",
                "quota": "cost",
                "period_end": period_end(admin),
                "plan": "free",
            }
        }
        assert "limit" not in response.text
        assert str(CAP) not in response.text
    assert rows_of(admin, alice.user_id) == (0, 0, 0)


async def test_a_cancel_gives_the_credit_back_once_and_frees_the_slot(
    admin: Connection, browsers: Any, env: Env, test_plan: Callable[..., None]
) -> None:
    alice: Browser = await browsers()
    test_plan(alice.user_id, classes_per_month=1, concurrent_jobs=2)
    created = await alice.create()
    assert created.status_code == 201
    job = created.json()["id"]
    assert (await alice.create()).status_code == 409  # the month is used
    cancels = await asyncio.gather(*(alice.post(f"/api/jobs/{job}/cancel") for _ in range(4)))
    assert {c.status_code for c in cancels} == {200}
    settled = await asyncio.gather(
        *(settle_one(env.factory, LEDGER, alice.user_id, UUID(job)) for _ in range(4))
    )
    assert sorted(r or "" for r in settled) == ["", "", "", "released"]  # once
    states = admin.execute(
        text("SELECT state, reason FROM credit_ledger WHERE owner_id = :o"), {"o": alice.user_id}
    ).all()
    assert [(s.state, s.reason) for s in states] == [("released", "cancelled")]
    assert held(admin, alice.user_id) == 0
    assert (await alice.create()).status_code == 201  # the slot is free at once
    assert held(admin, alice.user_id) == 1
    assert (await alice.create()).status_code == 409


async def test_an_admin_refund_frees_a_slot_is_audited_and_settles_once(
    admin: Connection, browsers: Any, env: Env, make_actor: Any, test_plan: Callable[..., None]
) -> None:
    actor = await stepped_admin(env, make_actor)
    alice: Browser = await browsers()
    test_plan(alice.user_id, classes_per_month=3, concurrent_jobs=16)
    credits = []
    for _ in range(3):
        job = new_job(admin, alice.user_id, "design_draft", "completed", credit_state="committed")
        credits.append(new_ledger(admin, alice.user_id, job, "committed"))
    assert (await alice.create()).status_code == 409
    path = f"/api/admin/users/{alice.user_id}/credits/{credits[0]}/refund"
    refunds = await asyncio.gather(*(actor.post(path) for _ in range(4)))
    assert statuses(refunds) == [200, 409, 409, 409], [r.text for r in refunds]
    assert held(admin, alice.user_id) == 2
    state = admin.execute(
        text("SELECT state, reason FROM credit_ledger WHERE id = :i"), {"i": credits[0]}
    ).one()
    assert (state.state, state.reason) == ("refunded", "admin_refund")
    assert sorted(audit(admin, actor.user_id)) == sorted(
        [("refund.credit", "ok", None)] + [("refund.credit", "refused", "already_refunded")] * 3
    )
    assert (await alice.create()).status_code == 201  # the refund freed exactly one slot
    assert (await alice.create()).status_code == 409


async def test_two_users_racing_at_once_never_affect_each_other(
    admin: Connection, browsers: Any, env: Env, test_plan: Callable[..., None]
) -> None:
    alice: Browser = await browsers()
    bob: Browser = await browsers()
    test_plan(alice.user_id, classes_per_month=LIMIT, concurrent_jobs=16)
    test_plan(bob.user_id)
    mixed = await asyncio.gather(*(u.create() for _ in range(N) for u in (alice, bob)))
    mine = [r for r, u in zip(mixed, [alice, bob] * N, strict=True) if u is alice]
    his = [r for r, u in zip(mixed, [alice, bob] * N, strict=True) if u is bob]
    assert statuses(mine) == [201] * LIMIT + [409] * (N - LIMIT)
    assert statuses(his) == [201] * LIMIT + [409] * (N - LIMIT)
    assert rows_of(admin, alice.user_id) == (LIMIT, LIMIT, LIMIT)
    assert rows_of(admin, bob.user_id) == (LIMIT, LIMIT, LIMIT)
    # Alice's cancel frees Alice's slot only.
    first = next(r for r in mine if r.status_code == 201).json()["id"]
    assert (await alice.post(f"/api/jobs/{first}/cancel")).status_code == 200
    assert await settle_one(env.factory, LEDGER, alice.user_id, UUID(first)) == "released"
    assert held(admin, alice.user_id) == LIMIT - 1
    assert held(admin, bob.user_id) == LIMIT
    assert (await bob.create()).status_code == 409
    assert (await alice.create()).status_code == 201


async def test_a_plan_limit_changed_by_the_admin_applies_to_the_next_reservation(
    admin: Connection, browsers: Any, env: Env, make_actor: Any
) -> None:
    actor = await stepped_admin(env, make_actor)
    alice: Browser = await browsers()
    for _ in range(2):
        job = new_job(admin, alice.user_id, "design_draft", "completed", credit_state="committed")
        new_ledger(admin, alice.user_id, job, "committed")
    plan = "/api/admin/plans/free"
    # Lowered to what she holds: the held credits stay, the next create is refused at once.
    assert (await actor.call("PATCH", plan, {"classes_per_month": 2})).status_code == 200
    refused = await alice.create()
    assert (refused.status_code, refused.json()) == (
        409, quota_body("free", 2, 2, period_end(admin)),
    )  # fmt: skip
    assert held(admin, alice.user_id) == 2
    # Raised by one: exactly one of a parallel burst is admitted.
    assert (await actor.call("PATCH", plan, {"classes_per_month": 3})).status_code == 200
    responses = await burst(alice, 6)
    assert statuses(responses) == [201] + [429] * 5  # free's one job at a time
    # The job limit is a plan limit too: raised, a burst admits as many as classes are left.
    await finish(env, admin, alice.user_id, "completed")
    assert (
        await actor.call("PATCH", plan, {"classes_per_month": 5, "concurrent_jobs": 2})
    ).status_code == 200
    two = await burst(alice, 6)
    assert set(statuses(two)[2:]) <= {409, 429}
    assert statuses(two).count(201) == 2  # two slots, and two classes left of five
    changes = [r for r in audit(admin, actor.user_id) if r[0] == "plan.update"]
    assert changes == [("plan.update", "ok", None)] * 3
