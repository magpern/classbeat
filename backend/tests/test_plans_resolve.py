"""``classbeat.plans.resolve`` (ADR 0011 section 2; issue DB1, #207): the effective plan is the
highest-ranked active entitlement's, ``free`` without one; revoked, future and ended entitlements
don't count; features are the plan's plus every active entitlement's."""

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classbeat.db import user_session
from classbeat.plans import Plan, effective_features, effective_plan
from ctx import make_context
from jobtools import engine_for, new_user

Factory = async_sessionmaker[AsyncSession]
FREE = Plan("free", "Free", 0, 3, 3, 1, 10_000_000, "default", frozenset())


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
async def factory(app_database_url: str) -> AsyncIterator[Factory]:
    engine = create_async_engine(app_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def grant(conn: Connection, owner: UUID, plan: str | None, **c: Any) -> None:
    """An entitlement written as the superuser (as classbeat_maint would grant one)."""
    values: dict[str, Any] = {"owner_id": owner, "plan_id": plan, "source": "grant", **c}
    times = {k: values.pop(k) for k in ("starts_at", "ends_at", "revoked_at") if k in values}
    names = ", ".join([*values, *times])
    params = ", ".join([f":{k}" for k in values] + [str(v) for v in times.values()])
    conn.execute(text(f"INSERT INTO entitlements ({names}) VALUES ({params})"), values)  # noqa: S608


async def resolved(factory: Factory, user: UUID) -> tuple[Plan, frozenset[str]]:
    ctx = make_context(user)
    async with user_session(factory, ctx) as db:
        return await effective_plan(db, ctx), await effective_features(db, ctx)


async def test_no_entitlement_is_free(admin: Connection, factory: Factory) -> None:
    assert await resolved(factory, new_user(admin)) == (FREE, frozenset())


async def test_the_highest_rank_wins_and_inactive_ones_are_ignored(
    admin: Connection, factory: Factory
) -> None:
    user, other = new_user(admin), new_user(admin)
    grant(admin, user, "instructor", ends_at="now() + interval '1 day'")
    grant(admin, user, "pro", revoked_at="now()")
    grant(admin, user, "pro", starts_at="now() + interval '1 hour'")
    grant(
        admin,
        user,
        "pro",
        starts_at="now() - interval '2 days'",
        ends_at="now() - interval '1 day'",
    )
    grant(admin, other, "pro")  # someone else's
    plan, _ = await resolved(factory, user)
    assert (plan.id, plan.classes_per_month, plan.revisions_per_class) == ("instructor", 30, 10)
    grant(admin, user, "pro", ends_at="now() + interval '1 day'")
    grant(admin, user, "free")
    assert (await resolved(factory, user))[0].id == "pro"
    assert (await resolved(factory, other))[0].id == "pro"


async def test_ended_and_revoked_entitlements_fall_back_to_free(
    admin: Connection, factory: Factory
) -> None:
    user = new_user(admin)
    grant(
        admin,
        user,
        "pro",
        starts_at="now() - interval '2 days'",
        ends_at="now() - interval '1 second'",
    )
    grant(admin, user, "instructor", revoked_at="now()", features=["song_picking"])
    assert await resolved(factory, user) == (FREE, frozenset())


async def test_features_are_the_union_of_the_plan_and_active_entitlements(
    admin: Connection, factory: Factory
) -> None:
    user = new_user(admin)
    grant(admin, user, None, features=["song_picking"])
    grant(admin, user, None, features=["song_picking"], ends_at="now() + interval '1 day'")
    plan, features = await resolved(factory, user)
    assert (plan, features) == (FREE, frozenset({"song_picking"}))
    # A plan's own features count too.
    admin.execute(text("UPDATE plans SET features = '{song_picking}' WHERE id = 'pro'"))
    other = new_user(admin)
    grant(admin, other, "pro")
    plan, features = await resolved(factory, other)
    assert (plan.id, plan.features, features) == (
        "pro",
        frozenset({"song_picking"}),
        frozenset({"song_picking"}),
    )
