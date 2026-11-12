"""#968 (#957 item 1, ADR 0031 section 2): the admin area's Users list and detail show each user's
plan as the resolver does. A stored admin is on the top plan with source ``admin`` and no end,
whatever their entitlements; a demoted or disabled admin and every other user keep what their
entitlements give. The role is read once per page (``classbeat.auth.stored_admins``), never per
row. Fakes only."""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine, make_url

from authtools import Env, running_env
from classbeat import auth
from plantools import grant

USERS = "/api/admin/users"
SOON = datetime.now(UTC) + timedelta(days=10)


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


async def stepped_admin(env: Env, make_actor: Any) -> Any:
    """The acting admin: TOTP in the last 12 hours and a fresh step-up."""
    user_id = await env.user("acting@example.com", role="admin")
    actor = await make_actor(user_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


@dataclass(frozen=True)
class Case:
    name: str
    admin: bool  # created with the stored role ``admin``
    demoted: bool = False  # then set back to ``user``
    disabled: bool = False
    plan: str | None = None  # an active entitlement naming this plan
    ends_at: datetime | None = None
    features: tuple[str, ...] = ()
    # Expected: (plan id, source, whether ends_at is set), and features included.
    expect: tuple[str, str | None, bool] = ("free", None, False)


CASES = [
    Case("admin-none", admin=True, expect=("pro", "admin", False)),
    Case(
        "admin-lower",
        admin=True,
        plan="instructor",
        ends_at=SOON,
        features=("song_picking",),
        expect=("pro", "admin", False),  # the entitlement's end is not the admin plan's
    ),
    Case("admin-same", admin=True, plan="pro", ends_at=SOON, expect=("pro", "admin", False)),
    Case("demoted-none", admin=True, demoted=True),
    Case(
        "demoted-lower",
        admin=True,
        demoted=True,
        plan="instructor",
        expect=("instructor", "grant", False),
    ),
    Case(  # the resolver reads no role for a disabled user: entitlements only
        "disabled-admin",
        admin=True,
        disabled=True,
        plan="instructor",
        ends_at=SOON,
        expect=("instructor", "grant", True),
    ),
    Case("user-none", admin=False),
    Case(
        "user-pro",
        admin=False,
        plan="pro",
        ends_at=SOON,
        features=("song_picking",),
        expect=("pro", "grant", True),
    ),
    Case("user-lower", admin=False, plan="instructor", expect=("instructor", "grant", False)),
]


def email(case: Case) -> str:
    return f"t-{case.name}@example.com"


async def make_users(env: Env, superuser: Connection, cases: list[Case]) -> dict[str, UUID]:
    ids: dict[str, UUID] = {}
    for case in cases:
        user_id = await env.user(email(case), role="admin" if case.admin else "user")
        if case.demoted:
            superuser.execute(text("UPDATE users SET role = 'user' WHERE id = :u"), {"u": user_id})
        if case.disabled:
            superuser.execute(
                text("UPDATE users SET disabled_at = now() WHERE id = :u"), {"u": user_id}
            )
        if case.plan is not None:
            grant(superuser, user_id, case.plan, ends_at=case.ends_at, features=list(case.features))
        ids[case.name] = user_id
    return ids


def plan_name(superuser: Connection, plan_id: str) -> str:
    return superuser.execute(
        text("SELECT name FROM plans WHERE id = :p"), {"p": plan_id}
    ).scalar_one()


def check(superuser: Connection, case: Case, row: dict[str, Any]) -> None:
    plan_id, source, has_end = case.expect
    assert row["plan"]["id"] == plan_id, case.name
    assert row["plan"]["name"] == plan_name(superuser, plan_id), case.name
    assert row["plan"]["source"] == source, case.name
    assert (row["plan"]["ends_at"] is not None) == has_end, case.name
    assert set(case.features) <= set(row["features"]), case.name  # the role takes none away
    limit = superuser.execute(
        text("SELECT classes_per_month FROM plans WHERE id = :p"), {"p": plan_id}
    ).scalar_one()
    assert row["classes_limit"] == limit, case.name


async def test_the_list_shows_each_user_as_the_resolver_does(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    top = superuser.execute(text("SELECT id FROM plans ORDER BY rank DESC LIMIT 1")).scalar_one()
    assert top == "pro"  # the cases name it as the admins' plan
    actor = await stepped_admin(env, make_actor)
    await make_users(env, superuser, CASES)
    response = await actor.get(f"{USERS}?q=t-&limit=100")
    assert response.status_code == 200, response.text
    rows = {u["email"]: u for u in response.json()["users"]}
    assert set(rows) == {email(c) for c in CASES}
    for case in CASES:
        check(superuser, case, rows[email(case)])
    acting = (await actor.get(f"{USERS}?q=acting")).json()["users"]
    assert [(u["plan"]["id"], u["plan"]["source"]) for u in acting] == [("pro", "admin")]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_the_detail_shows_the_same_plan(
    env: Env, make_actor: Any, superuser: Connection, case: Case
) -> None:
    actor = await stepped_admin(env, make_actor)
    ids = await make_users(env, superuser, [case])
    response = await actor.get(f"{USERS}/{ids[case.name]}")
    assert response.status_code == 200, response.text
    check(superuser, case, response.json())


@pytest.mark.parametrize("case", [c for c in CASES if not c.disabled], ids=lambda c: c.name)
async def test_the_admin_view_agrees_with_the_users_own_plan(
    env: Env, make_actor: Any, superuser: Connection, case: Case
) -> None:
    """The user's own ``/api/account/plan`` (``classbeat.plans.resolve``) is the reference."""
    actor = await stepped_admin(env, make_actor)
    ids = await make_users(env, superuser, [case])
    own = (await (await make_actor(ids[case.name])).get("/api/account/plan")).json()
    shown = (await actor.get(f"{USERS}/{ids[case.name]}")).json()
    assert (shown["plan"]["id"], shown["plan"]["source"]) == (own["plan"], own["source"])
    assert shown["plan"]["ends_at"] == own["ends_at"]


async def test_stored_admins_matches_stored_admin(env: Env, superuser: Connection) -> None:
    ids = await make_users(env, superuser, CASES)
    everyone = [*ids.values(), uuid4()]  # and one id that is no user
    async with env.db() as db:
        one_by_one = {u for u in everyone if await auth.stored_admin(db, u)}
        assert await auth.stored_admins(db, everyone) == one_by_one
        assert await auth.stored_admins(db, []) == frozenset()
    assert one_by_one == {ids["admin-none"], ids["admin-lower"], ids["admin-same"]}


@contextmanager
def counted_statements() -> Iterator[list[str]]:
    seen: list[str] = []

    def count(*args: Any) -> None:
        seen.append(args[2])

    event.listen(Engine, "before_cursor_execute", count)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", count)


async def test_the_list_reads_the_roles_once_per_page_whatever_its_size(
    env: Env, make_actor: Any, superuser: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await stepped_admin(env, make_actor)
    await make_users(env, superuser, CASES)
    batches: list[int] = []
    real = auth.stored_admins

    async def spy(db: Any, user_ids: Any) -> frozenset[UUID]:
        batches.append(len(user_ids))
        return await real(db, user_ids)

    async def per_row(*_: Any) -> bool:
        raise AssertionError("the list must not look up one role at a time")

    # A first request warms what a session does once (its first use, the pool's connections).
    assert (await actor.get(f"{USERS}?q=t-&limit=1")).status_code == 200
    monkeypatch.setattr(auth, "stored_admins", spy)
    monkeypatch.setattr(auth, "stored_admin", per_row)
    with counted_statements() as small:
        assert (await actor.get(f"{USERS}?q=t-&limit=2")).status_code == 200
    with counted_statements() as large:
        assert (await actor.get(f"{USERS}?q=t-&limit=100")).status_code == 200
    assert batches == [2, len(CASES)]
    assert len(small) == len(large)  # no statement per row
