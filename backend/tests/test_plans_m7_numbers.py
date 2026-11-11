"""M7a-2 (#947, ADR 0031 sections 1, 2 and 8): the three plans' names, numbers and gates, admins
on the top plan, the quality words and the public plan catalogue. The suites' world keeps the
seeds of migration 0007 (``dbtools.truncate_auth``); the tests that are about the new numbers
apply migration 0045's seed first. Fakes only."""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classbeat import auth
from classbeat.config import Settings
from classbeat.db import system_session, user_session
from classbeat.flags.effective import song_picking_admits
from classbeat.jobs.credits import QuotaRefused
from classbeat.jobs.inputs import ModelJobInput
from classbeat.jobs.kinds import Admitted
from classbeat.plans import effective_features, effective_plan
from classbeat.plans.quality_model import quality_for
from classbeat.quota import bounds as quota_bounds
from classbeat.quota.bounds import DEFAULT_JOBS, Bounds
from classbeat.quota.gates import check_brief, gates_for
from classbeat.quota.route import with_plan, with_plan_run
from ctx import make_context
from dbtools import migration_module
from jobapi import Browser
from jobtools import engine_for, new_user
from plantools import grant

Factory = async_sessionmaker[AsyncSession]
MIGRATION = migration_module("plan_names_and_numbers")
NEW = {
    "free": ("Free", 3, 3, 1, 30, False, False, True),
    "instructor": ("Individual", 9, 10, 2, 75, True, True, True),
    "pro": ("Professional", 30, 20, 3, 180, True, True, True),
}
COLUMNS = (
    "name, classes_per_month, revisions_per_class, concurrent_jobs, max_class_minutes, "
    "games_allowed, ftp_tests_allowed, races_allowed"
)


@pytest.fixture
def users_plan() -> None:
    """The browser tests start on ``free`` (no grant)."""
    return None


@pytest.fixture
def superuser(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def m7(superuser: Connection) -> Iterator[Connection]:
    """The plans as migration 0045 sets them (the next ``truncate_auth`` seeds the old ones)."""
    superuser.execute(text(MIGRATION.SEED))
    try:
        yield superuser
    finally:
        superuser.execute(text(MIGRATION.RESTORE))


@pytest.fixture
async def factory(app_database_url: str) -> AsyncIterator[Factory]:
    engine = create_async_engine(app_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def soon(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def set_role(conn: Connection, user: UUID, role: str) -> None:
    conn.execute(text("UPDATE users SET role = :r WHERE id = :u"), {"r": role, "u": user})


async def plan_id(factory: Factory, user: UUID, role: str = "user") -> str:
    ctx = make_context(user, role=role)
    async with user_session(factory, ctx) as db:
        return (await effective_plan(db, ctx)).id


# --- the migration's rows, the default and the quality words ---------------------------------


def test_the_new_numbers_are_in_the_seed_and_the_downgrade_restores_the_old(
    m7: Connection,
) -> None:
    rows = m7.execute(text(f"SELECT id, {COLUMNS} FROM plans ORDER BY rank")).all()  # noqa: S608
    assert {r[0]: tuple(r[1:]) for r in rows} == NEW
    m7.execute(text(MIGRATION.RESTORE))
    rows = m7.execute(text(f"SELECT id, {COLUMNS} FROM plans ORDER BY rank")).all()  # noqa: S608
    assert {r[0]: tuple(r[1:]) for r in rows} == {
        "free": ("Free", 3, 3, 1, 30, False, True, True),
        "instructor": ("Instructor", 30, 10, 2, 60, True, True, True),
        "pro": ("Pro", 100, 25, 3, 90, True, True, True),
    }


def test_three_jobs_at_once_is_the_default_and_professional_is_not_clamped() -> None:
    assert Settings.model_fields["jobs_per_user"].default == 3
    assert DEFAULT_JOBS == 3
    assert Bounds().jobs_per_user == 3


def test_the_quality_follows_the_plan() -> None:
    assert [quality_for(p) for p in ("free", "instructor", "pro")] == ["standard", "better", "best"]
    assert quality_for("later") == "standard"  # a plan the table does not name yet: the lowest


# --- admins are Professional ------------------------------------------------------------------


async def test_an_admin_without_an_entitlement_is_professional_and_a_demotion_applies_at_once(
    m7: Connection, factory: Factory
) -> None:
    admin, ordinary = new_user(m7), new_user(m7)
    set_role(m7, admin, "admin")
    assert await plan_id(factory, admin) == "pro"
    assert await plan_id(factory, ordinary) == "free"
    set_role(m7, admin, "user")  # the CLI's demotion
    assert await plan_id(factory, admin) == "free"
    set_role(m7, admin, "admin")
    assert await plan_id(factory, admin) == "pro"


async def test_the_stored_role_decides_not_the_contexts(m7: Connection, factory: Factory) -> None:
    user = new_user(m7)
    assert await plan_id(factory, user, role="admin") == "free"  # a context's role grants nothing
    set_role(m7, user, "admin")
    assert await plan_id(factory, user, role="user") == "pro"


async def test_a_non_admin_keeps_what_the_entitlements_give(
    m7: Connection, factory: Factory
) -> None:
    user, other = new_user(m7), new_user(m7)
    grant(m7, user, "pro", ends_at=soon(1))
    assert await plan_id(factory, user) == "pro"
    assert await plan_id(factory, other) == "free"
    grant(m7, other, "instructor")
    assert await plan_id(factory, other) == "instructor"


async def test_stored_admin_is_false_without_a_row(m7: Connection, factory: Factory) -> None:
    gone, disabled, admin = new_user(m7), new_user(m7), new_user(m7)
    set_role(m7, disabled, "admin")
    m7.execute(text("UPDATE users SET disabled_at = now() WHERE id = :u"), {"u": disabled})
    set_role(m7, admin, "admin")
    async with system_session(factory) as db:
        assert await auth.stored_admin(db, admin) is True
        assert await auth.stored_admin(db, disabled) is False  # disabled: no row, never True
        assert await auth.stored_admin(db, uuid4()) is False  # missing
        assert await auth.stored_admin(db, gone) is False  # a user, not an admin
    # A disabled admin falls back to their entitlements, free here.
    assert await plan_id(factory, disabled) == "free"


async def test_an_admins_features_are_unchanged_and_song_picking_stays_plan_independent(
    m7: Connection, factory: Factory
) -> None:
    admin, plain = new_user(m7), new_user(m7)
    set_role(m7, admin, "admin")
    ctx = make_context(admin, role="admin")
    async with user_session(factory, ctx) as db:
        assert await effective_features(db, ctx) == frozenset()  # no song_picking, no billing_test
    assert m7.execute(text("SELECT count(*) FROM plans WHERE features <> '{}'")).scalar_one() == 0
    # ``granted``: only an entitlement's feature admits, whatever plan the user is on.
    m7.execute(
        text("INSERT INTO app_settings (key, value) VALUES ('song_picking', CAST(:v AS jsonb))"),
        {"v": json.dumps("granted")},
    )
    try:
        answers: dict[str, bool] = {}
        for plan in ("free", "instructor", "pro"):
            m7.execute(text("DELETE FROM entitlements WHERE owner_id = :o"), {"o": plain})
            if plan != "free":
                grant(m7, plain, plan)
            ctx = make_context(plain)
            async with user_session(factory, ctx) as db:
                answers[plan] = await song_picking_admits(db, ctx, True)
        assert answers == {"free": False, "instructor": False, "pro": False}
        grant(m7, plain, "pro", features=["song_picking"])
        async with user_session(factory, ctx) as db:
            assert await song_picking_admits(db, ctx, True) is True
        ctx = make_context(admin, role="admin")
        async with user_session(factory, ctx) as db:
            assert await song_picking_admits(db, ctx, True) is False  # the role grants none
    finally:
        m7.execute(text("DELETE FROM app_settings WHERE key = 'song_picking'"))


async def test_the_web_and_the_worker_resolve_an_admin_to_professional(
    m7: Connection, factory: Factory
) -> None:
    from pydantic import BaseModel

    class Nothing(BaseModel):
        pass

    admin = new_user(m7)
    set_role(m7, admin, "admin")
    ctx = make_context(admin)
    async with user_session(factory, ctx) as db:
        admitted = await with_plan()(db, ctx, Nothing())
    assert isinstance(admitted, Admitted)
    assert admitted.fields["route_key"] == "pro"
    assert admitted.fields["plan_gates"]["max_class_minutes"] == 180

    seen: list[Any] = []

    async def inner(job_run: Any) -> str:
        seen.append(job_run.input.plan_gates)
        return "ran"

    @asynccontextmanager
    async def fenced() -> AsyncIterator[Any]:
        async with user_session(factory, ctx) as db:
            yield SimpleNamespace(db=db)

    run = SimpleNamespace(input=ModelJobInput(title="Tuesday"), ctx=ctx, fenced=fenced)
    assert await with_plan_run(inner)(run) == "ran"  # pyright: ignore[reportArgumentType]
    assert seen[0].max_class_minutes == 180
    assert seen[0].ftp_tests is True
    # Demoted: the next statement is Free's (the worker reads the plan on every attempt).
    set_role(m7, admin, "user")
    run = SimpleNamespace(input=ModelJobInput(title="Tuesday"), ctx=ctx, fenced=fenced)
    await with_plan_run(inner)(run)  # pyright: ignore[reportArgumentType]
    assert seen[1].max_class_minutes == 30
    assert seen[1].ftp_tests is False


# --- the gates of the new numbers ------------------------------------------------------------


async def gates_of(factory: Factory, user: UUID) -> tuple[Any, Any]:
    ctx = make_context(user)
    async with user_session(factory, ctx) as db:
        plan = await effective_plan(db, ctx)
    return plan, gates_for(plan, quota_bounds.current())


def refusal(gates: Any, plan: Any, **brief: Any) -> dict[str, Any] | None:
    try:
        check_brief(gates, {"length_min": 30, **brief}, plan.id)
    except QuotaRefused as refused:
        return refused.detail()
    return None


async def test_free_refuses_an_ftp_test_admits_a_race_and_individual_caps_at_75(
    m7: Connection, factory: Factory
) -> None:
    free_user, individual = new_user(m7), new_user(m7)
    grant(m7, individual, "instructor")
    plan, gates = await gates_of(factory, free_user)
    assert refusal(gates, plan, ftp_test=20) == {
        "quota": "feature", "feature": "ftp_tests", "plan": "free",
    }  # fmt: skip
    assert refusal(gates, plan, class_type="ftp_test") is not None
    assert refusal(gates, plan, races=3) is None  # races are on every plan
    assert refusal(gates, plan, length_min=30) is None
    assert refusal(gates, plan, length_min=31) is not None
    plan, gates = await gates_of(factory, individual)
    assert refusal(gates, plan, length_min=75, ftp_test=20, races=2) is None
    assert refusal(gates, plan, length_min=76) == {
        "quota": "length", "limit": 75, "used": 76, "plan": "instructor",
    }  # fmt: skip


# --- GET /api/account/plan and GET /api/plans -----------------------------------------------


async def test_the_account_plan_reports_the_admin_source_and_the_quality(
    m7: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    body = (await alice.get("/api/account/plan")).json()
    assert (body["plan"], body["name"], body["quality"], body["source"]) == (
        "free", "Free", "standard", None,
    )  # fmt: skip
    set_role(m7, alice.user_id, "admin")
    body = (await alice.get("/api/account/plan")).json()
    assert (body["plan"], body["name"], body["quality"]) == ("pro", "Professional", "best")
    assert (body["source"], body["ends_at"], body["features"]) == ("admin", None, [])
    assert body["limits"]["concurrent_jobs"] == 2  # under the suites' environment bound
    assert body["gates"] == {"games": True, "races": True, "ftp_tests": True}
    set_role(m7, alice.user_id, "user")
    assert (await alice.get("/api/account/plan")).json()["source"] is None
    grant(m7, alice.user_id, "instructor", ends_at=soon(2))
    body = (await alice.get("/api/account/plan")).json()
    assert (body["plan"], body["name"], body["quality"], body["source"]) == (
        "instructor", "Individual", "better", "grant",
    )  # fmt: skip


async def test_the_plan_catalogue_is_public_and_has_no_price(m7: Connection, env: Any) -> None:
    # A planted price the app role could not read anyway: it must never reach the answer.
    m7.execute(
        text(
            "UPDATE plans SET monthly_price_cents = 1234, intro_price_cents = 99, "
            "intro_price_until = DATE '2030-01-01', currency = 'EUR' WHERE id = 'pro'"
        )
    )
    try:
        response = await env.client.get("/api/plans")  # signed out
    finally:
        m7.execute(
            text(
                "UPDATE plans SET monthly_price_cents = NULL, intro_price_cents = NULL, "
                "intro_price_until = NULL, currency = NULL"
            )
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["id"] for p in body["plans"]] == ["free", "instructor", "pro"]
    assert [p["name"] for p in body["plans"]] == ["Free", "Individual", "Professional"]
    assert [p["quality"] for p in body["plans"]] == ["standard", "better", "best"]
    assert [p["rank"] for p in body["plans"]] == sorted(p["rank"] for p in body["plans"])
    free, individual, professional = body["plans"]
    assert free["limits"] == {
        "classes_per_month": 3,
        "revisions_per_class": 3,
        "concurrent_jobs": 1,
        "max_class_minutes": 30,
    }
    assert individual["limits"]["max_class_minutes"] == 75
    assert professional["limits"] == {
        "classes_per_month": 30,
        "revisions_per_class": 20,
        "concurrent_jobs": 2,  # under the suites' environment bound of 2
        "max_class_minutes": 180,
    }
    assert [p["gates"] for p in body["plans"]] == [
        {"games": False, "races": True, "ftp_tests": False},
        {"games": True, "races": True, "ftp_tests": True},
        {"games": True, "races": True, "ftp_tests": True},
    ]
    raw = response.text.lower()
    for word in ("price", "cents", "currency", "intro", "1234", "eur", "cost", "micros"):
        assert word not in raw, word
    assert response.headers["cache-control"] == "no-store"
