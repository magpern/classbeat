"""The jobs API with the metered ledger and the plan's limits (ADR 0011 sections 3, 4 and 9;
QT1, #214). Fakes only.

- 409 ``quota`` carries FE1's body (``quota``, ``limit``, ``used``, ``period_end``, ``plan``;
  no money for ``cost``) and creates nothing; 429 ``busy`` carries the plan's ``limit``.
- The cost cap refuses create, approve, ``revise_draft`` and ``revise_songs``.
- Parallel approvals never pass the plan's ``concurrent_jobs``.
- ``GET /api/account/plan`` and the admin user page show the limits under the environment's
  bounds (this module's app runs with ``CLASSBEAT_CLASSES_PER_MONTH_MAX`` 50).
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat.plans.account import account_plan
from classbeat.quota import Bounds
from classbeat.quota.bounds import next_month
from ctx import make_context
from jobapi import LIST_HASH, Browser, plant_running, waiting_job
from jobcore import JobsEnv
from quotatools import Factory, admin, count, factory, test_plan
from revisesongtools import change, picked
from revisetools import ReviseEnv
from songtools import SongEnv
from test_plans_routes import plant_ledger, plant_run


@pytest.fixture
def users_plan() -> None:
    """These tests are about the plan itself: their users start on ``free`` (no grant)."""
    return None


__all__ = ["admin", "factory", "test_plan"]  # fixtures

CAP = 10_000_000  # free's monthly cost
CLASSES_MAX = 50


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    """The app with the admin area and a bound below Pro's 100 classes."""
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        classes_per_month_max=CLASSES_MAX,
    ) as running:
        yield running


def period_end(conn: Connection) -> str:
    first: date = conn.execute(
        text("SELECT CAST(date_trunc('month', now() AT TIME ZONE 'UTC') AS date)")
    ).scalar_one()
    return next_month(first).isoformat()


def nothing_of(conn: Connection, owner: UUID) -> tuple[int, int]:
    return (
        count(conn, "SELECT count(*) FROM classes WHERE owner_id = :o", o=owner),
        count(conn, "SELECT count(*) FROM jobs WHERE owner_id = :o", o=owner),
    )


async def test_a_create_over_the_month_answers_409_quota_with_the_numbers(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers()
    for _ in range(3):
        plant_ledger(admin, alice.user_id, "committed")
    refused = await alice.create()
    assert (refused.status_code, refused.json()) == (
        409,
        {
            "detail": {
                "reason": "quota",
                "quota": "classes",
                "limit": 3,
                "used": 3,
                "period_end": period_end(admin),
                "plan": "free",
            }
        },
    )
    assert nothing_of(admin, alice.user_id) == (0, 0)


async def test_a_create_at_the_cost_cap_answers_409_quota_cost_without_money(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers()
    plant_run(admin, alice.user_id, CAP)
    refused = await alice.create()
    assert (refused.status_code, refused.json()) == (
        409,
        {
            "detail": {
                "reason": "quota",
                "quota": "cost",
                "period_end": period_end(admin),
                "plan": "free",
            }
        },
    )
    assert nothing_of(admin, alice.user_id) == (0, 0)


async def test_busy_answers_429_with_the_plans_limit(
    admin: Connection, browsers: Any, jenv: JobsEnv, test_plan: Callable[..., None]
) -> None:
    alice: Browser = await browsers()
    plant_running(jenv, alice.user_id)  # free: one at a time
    busy = await alice.create()
    assert (busy.status_code, busy.json()) == (429, {"detail": {"reason": "busy", "limit": 1}})
    test_plan(alice.user_id, concurrent_jobs=16)  # the environment's 2 bounds it
    assert (await alice.create()).status_code == 201
    busy = await alice.create()
    assert (busy.status_code, busy.json()) == (429, {"detail": {"reason": "busy", "limit": 2}})


async def test_an_approval_at_the_cost_cap_or_the_job_limit_is_refused(
    admin: Connection, browsers: Any, jenv: JobsEnv
) -> None:
    alice: Browser = await browsers()
    job_id, revision = await waiting_job(jenv, alice)
    plant_run(admin, alice.user_id, CAP)
    refused = await alice.approve(job_id, revision, LIST_HASH)
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        {"reason": "quota", "quota": "cost", "period_end": period_end(admin), "plan": "free"},
    )
    admin.execute(text("DELETE FROM agent_runs WHERE owner_id = :o"), {"o": alice.user_id})
    running = plant_running(jenv, alice.user_id)
    busy = await alice.approve(job_id, revision, LIST_HASH)
    assert (busy.status_code, busy.json()) == (429, {"detail": {"reason": "busy", "limit": 1}})
    admin.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": running})
    assert jenv.row(job_id).state == "waiting_for_approval"
    approved = await alice.approve(job_id, revision, LIST_HASH)
    assert approved.status_code == 200, approved.text


async def test_parallel_approvals_never_pass_the_plans_concurrent_jobs(
    admin: Connection, browsers: Any, jenv: JobsEnv, test_plan: Callable[..., None]
) -> None:
    alice: Browser = await browsers()
    test_plan(alice.user_id, classes_per_month=10, concurrent_jobs=2)
    waiting = [await waiting_job(jenv, alice) for _ in range(4)]
    answers = await asyncio.gather(*(alice.approve(j, r, LIST_HASH) for j, r in waiting))
    assert sorted(a.status_code for a in answers) == [200, 200, 429, 429]
    assert {a.json()["detail"]["limit"] for a in answers if a.status_code == 429} == {2}
    states = [jenv.row(j).state for j, _ in waiting]
    assert sorted(states) == ["queued", "queued", "waiting_for_approval", "waiting_for_approval"]


async def test_revise_draft_at_the_cost_cap_is_refused_and_creates_nothing(
    admin: Connection, renv: ReviseEnv
) -> None:
    class_id, base = await renv.design()
    plant_run(admin, renv.owner, CAP)
    before = (len(renv.messages(class_id)), nothing_of(admin, renv.owner))
    refused = await renv.ask(class_id, base)
    assert (refused.status_code, refused.json()["detail"]["quota"]) == (409, "cost")
    assert (len(renv.messages(class_id)), nothing_of(admin, renv.owner)) == before


async def test_revise_songs_at_the_cost_cap_is_refused_and_creates_nothing(
    songenv: SongEnv,
) -> None:
    rider = await songenv.rider()
    class_id, base, _ = await picked(songenv, rider)
    plant_run(songenv.admin, rider.user_id, CAP)
    jobs = "SELECT count(*) FROM jobs WHERE owner_id = :o"
    before = (songenv.sql(jobs, o=rider.user_id).scalar_one(), songenv.counts())
    refused = await change(rider, class_id, base)
    assert (refused.status_code, refused.json()["detail"]["quota"]) == (409, "cost")
    assert (songenv.sql(jobs, o=rider.user_id).scalar_one(), songenv.counts()) == before


async def test_the_account_plan_shows_the_limits_under_the_bounds(
    admin: Connection, factory: Factory, test_plan: Callable[..., None]
) -> None:
    from jobtools import new_user

    owner = new_user(admin)
    test_plan(owner, classes_per_month=500, concurrent_jobs=16, revisions_per_class=200)
    plant_run(admin, owner, 1_000_000)
    bounds = Bounds(classes_per_month_max=50, monthly_cost_micros_max=2_000_000)
    found = await account_plan(factory, make_context(owner), bounds=bounds, trial_days_max=30)
    assert (found.limits.classes_per_month, found.limits.concurrent_jobs) == (50, 2)
    assert (found.limits.revisions_per_class, found.cost_share) == (25, 0.5)


async def test_the_admin_user_page_shows_the_effective_class_limit(
    env: Env, make_actor: Any, admin: Connection
) -> None:
    from test_admin_users import USERS, stepped_admin

    actor = await stepped_admin(env, make_actor, "admin@example.com")
    pro = await env.user("pro@example.com")
    admin.execute(
        text("INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant')"),
        {"o": pro},
    )
    listed = await actor.get(f"{USERS}?q=pro")
    assert listed.status_code == 200, listed.text
    assert listed.json()["users"][0]["classes_limit"] == CLASSES_MAX  # Pro stores 100
    one = await actor.get(f"{USERS}/{pro}")
    assert one.json()["classes_limit"] == CLASSES_MAX
