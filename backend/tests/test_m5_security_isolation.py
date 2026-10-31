"""M5 security suite, isolation and the ledger trigger (ADR 0011 sections 1 and 10; SEC1, #227).
Fakes only.

- T89, T96: the four owned M5 tables answer only for the context's user (no user, no rows);
  writes aimed at another user's rows are refused or change nothing; the account endpoints carry
  no id to swap, and one user's plan, ledger, trial and redemptions never show in another's.
- T96: the ledger trigger decides by ``current_user`` and its function is ``SECURITY INVOKER``
  (a definer would make every settle the owner's, and refused); the app role cannot switch the
  trigger, row security or a policy off, take another role, or replace the function.
"""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from classbeat.db import system_session, user_session
from classbeat.plans import routes
from ctx import make_context
from jobapi import Browser
from jobtools import as_user, engine_for, new_job, new_user
from plantools import grant, plant_code, set_trial
from test_plans_schema import REFUSED, ledger, row


@pytest.fixture
def users_plan() -> None:
    """These tests are about the plan itself: their users start on ``free`` (no grant)."""
    return None


OWNED = ("entitlements", "code_redemptions", "credit_ledger", "quota_periods")
PERIOD = "CAST(date_trunc('month', now()) AS date)"
LAST_PERIOD = "CAST(date_trunc('month', now()) - interval '1 month' AS date)"


def plant_owned(conn: Connection, owner: UUID) -> dict[str, UUID]:
    """One row of each owned M5 table for ``owner``, as the superuser."""
    _, code_id = plant_code(conn, uses=10)
    entitlement = conn.execute(
        text(
            "INSERT INTO entitlements (owner_id, plan_id, source, code_id, ends_at) "
            "VALUES (:o, 'instructor', 'code', :c, now() + interval '1 day') RETURNING id"
        ),
        {"o": owner, "c": code_id},
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO code_redemptions (owner_id, code_id, entitlement_id) VALUES (:o, :c, :e)"
        ),
        {"o": owner, "c": code_id, "e": entitlement},
    )
    conn.execute(
        text(f"INSERT INTO quota_periods (owner_id, period) VALUES (:o, {PERIOD})"),  # noqa: S608
        {"o": owner},
    )
    return {
        "entitlement": entitlement,
        "code": code_id,
        "ledger": ledger(conn, owner, new_job(conn, owner)),
    }


async def test_t89_owned_m5_rows_answer_only_for_their_owner(
    admin: Connection, browsers: Any, env: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    bob: Browser = await browsers(mowl=False)
    plant_owned(admin, alice.user_id)
    plant_owned(admin, bob.user_id)
    for user in (alice.user_id, bob.user_id):
        async with user_session(env.factory, make_context(user)) as db:
            for table in OWNED:
                owners = (
                    (
                        await db.execute(text(f"SELECT owner_id FROM {table}"))  # noqa: S608
                    )
                    .scalars()
                    .all()
                )
                assert owners == [user], table
    async with system_session(env.factory) as db:
        for table in OWNED:
            assert (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0  # noqa: S608


def as_app(app: Engine, user: UUID, statement: str, **params: Any) -> int:
    with app.begin() as connection:
        as_user(connection, user)
        return connection.execute(text(statement), params).rowcount


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url) as engine:
        yield engine


async def test_t89_t96_writes_aimed_at_another_users_rows_are_refused(
    admin: Connection, browsers: Any, app: Engine
) -> None:
    alice: Browser = await browsers(mowl=False)
    bob: Browser = await browsers(mowl=False)
    theirs = plant_owned(admin, bob.user_id)
    _, code_id = plant_code(admin)
    for statement in (
        "INSERT INTO entitlements (owner_id, plan_id, source, code_id, ends_at) "
        "VALUES (:b, 'pro', 'code', :c, now() + interval '1 day')",
        "INSERT INTO entitlements (owner_id, plan_id, source, ends_at) "
        "VALUES (:b, 'instructor', 'trial', now() + interval '1 day')",
        "INSERT INTO code_redemptions (owner_id, code_id, entitlement_id) VALUES (:b, :c, :e)",
        "INSERT INTO credit_ledger (owner_id, period, plan_id, state) "  # noqa: S608
        f"VALUES (:b, {PERIOD}, 'free', 'reserved')",
        f"INSERT INTO quota_periods (owner_id, period) VALUES (:b, {LAST_PERIOD})",  # noqa: S608
    ):
        with pytest.raises(DBAPIError, match="row-level security"):
            as_app(app, alice.user_id, statement, b=bob.user_id, c=code_id, e=theirs["entitlement"])
    # Bob's rows are invisible to an update in Alice's context: nothing changes.
    for statement in (
        "UPDATE credit_ledger SET state = 'released', reason = 'cancelled', settled_at = now() "
        "WHERE id = :i",
        "UPDATE credit_ledger SET state = 'committed', settled_at = now() WHERE owner_id = :b",
        "UPDATE quota_periods SET period = period WHERE owner_id = :b",
    ):
        assert as_app(app, alice.user_id, statement, i=theirs["ledger"], b=bob.user_id) == 0
    assert row(admin, theirs["ledger"]).state == "reserved"
    assert (
        admin.execute(
            text("SELECT count(*) FROM entitlements WHERE owner_id = :b"), {"b": bob.user_id}
        ).scalar_one()
        == 1
    )


def test_the_account_endpoints_carry_no_id_to_swap() -> None:
    """Cross-user 404s apply to routes that take an id; the account plan API takes none (it is
    always the session's own user). A route added here with a path parameter fails this test,
    and needs a cross-user 404 test of its own.

    Threats: T89.
    """
    paths = [getattr(r, "path", "") for r in routes.router.routes]
    assert sorted(paths) == ["/api/account/plan", "/api/account/redeem", "/api/account/trial"]
    assert not [p for p in paths if "{" in p]


async def test_t89_one_users_plan_ledger_trial_and_codes_never_show_in_anothers(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    bob: Browser = await browsers(mowl=False)
    set_trial(admin, {"plan": "instructor", "days": 14})
    assert (await alice.post("/api/account/trial")).status_code == 200
    grant(admin, alice.user_id, "pro", features=["song_picking"])
    for _ in range(2):
        ledger(admin, alice.user_id, new_job(admin, alice.user_id), state="committed",
               settled_at="2026-10-02")  # fmt: skip
    code, code_id = plant_code(admin, uses=2)
    assert (await alice.post("/api/account/redeem", {"code": code})).status_code == 200
    body = (await bob.get("/api/account/plan")).json()
    assert (body["plan"], body["source"], body["classes_held"], body["features"]) == (
        "free",
        None,
        0,
        [],
    )
    assert body["trial"] == {"plan": "instructor", "days": 14}  # Alice's trial is not Bob's
    # Alice's redemption is not Bob's: he redeems the same code once, then it is his own.
    assert (await bob.post("/api/account/redeem", {"code": code})).status_code == 200
    again = await bob.post("/api/account/redeem", {"code": code})
    assert (again.status_code, again.json()["detail"]["reason"]) == (409, "already_redeemed")
    assert (await bob.post("/api/account/trial")).status_code == 200
    held = admin.execute(
        text("SELECT owner_id, count(*) FROM code_redemptions WHERE code_id = :c GROUP BY 1"),
        {"c": code_id},
    ).all()
    assert sorted((r[0], r[1]) for r in held) == sorted([(alice.user_id, 1), (bob.user_id, 1)])


# --- T96: the ledger trigger -------------------------------------------------------------------


@pytest.fixture
def superuser(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def transactional(database_url: str) -> Iterator[Engine]:
    """The superuser with real transactions (``SET LOCAL`` and rolled-back DDL need them)."""
    with engine_for(database_url) as engine:
        yield engine


SETTLE = "UPDATE credit_ledger SET state = 'committed', settled_at = now() WHERE id = :i"


def settle_as(engine: Engine, role: str, user: UUID, entry: UUID) -> int:
    """Settle in a transaction of its own as ``role`` (``SET LOCAL ROLE`` from the superuser, so
    ``session_user`` stays the superuser and only ``current_user`` changes)."""
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {role}"))
        as_user(conn, user)
        return conn.execute(text(SETTLE), {"i": entry}).rowcount


def test_t96_the_trigger_reads_current_user_not_session_user(
    superuser: Connection, transactional: Engine
) -> None:
    user = new_user(superuser)
    entry = ledger(superuser, user, new_job(superuser, user))
    with pytest.raises(DBAPIError, match=REFUSED):
        settle_as(transactional, "classbeat_owner", user, entry)
    with pytest.raises(DBAPIError, match="refused for role postgres"):
        settle_as(transactional, "postgres", user, entry)
    assert settle_as(transactional, "classbeat_app", user, entry) == 1
    assert row(superuser, entry).state == "committed"


def test_t96_the_trigger_function_is_invoker_pinned_and_enabled(superuser: Connection) -> None:
    fn = superuser.execute(
        text(
            "SELECT p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner) AS owner, "
            "t.tgenabled, t.tgtype FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE t.tgname = 'credit_ledger_guard' "
            "AND t.tgrelid = 'public.credit_ledger'::regclass"
        )
    ).one()
    assert (fn.prosecdef, fn.proconfig, fn.owner) == (
        False,
        ["search_path=pg_catalog, pg_temp"],
        "classbeat_owner",
    )
    # Enabled in origin mode; a row trigger (bit 0), BEFORE (bit 1), on UPDATE only (bit 4).
    assert (fn.tgenabled, fn.tgtype) == ("O", 1 | 2 | 16)


def test_t96_a_security_definer_trigger_would_refuse_every_settle(
    superuser: Connection, transactional: Engine
) -> None:
    """Why ``SECURITY INVOKER`` is load-bearing: as a definer, ``current_user`` inside the trigger
    is the function's owner, so the app role's settle is refused. (Rolled back.)"""
    user = new_user(superuser)
    entry = ledger(superuser, user, new_job(superuser, user))
    with transactional.connect() as conn, conn.begin() as tx:
        conn.execute(text("ALTER FUNCTION public.credit_ledger_guard() SECURITY DEFINER"))
        conn.execute(text("SET LOCAL ROLE classbeat_app"))
        as_user(conn, user)
        with (
            pytest.raises(DBAPIError, match="refused for role classbeat_owner"),
            conn.begin_nested(),
        ):
            conn.execute(text(SETTLE), {"i": entry})
        tx.rollback()
    assert row(superuser, entry).state == "reserved"
    assert (
        superuser.execute(
            text("SELECT prosecdef FROM pg_proc WHERE proname = 'credit_ledger_guard'")
        ).scalar_one()
        is False
    )


@pytest.mark.parametrize(
    "statement",
    [
        "SET ROLE classbeat_owner",
        "SET ROLE postgres",
        "SET session_replication_role = replica",  # would silence every trigger
        "ALTER TABLE credit_ledger DISABLE TRIGGER credit_ledger_guard",
        "ALTER TABLE credit_ledger DISABLE TRIGGER ALL",
        "ALTER TABLE credit_ledger NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE entitlements DISABLE ROW LEVEL SECURITY",
        "DROP POLICY entitlements_app_sources ON entitlements",
        "DROP POLICY credit_ledger_settle ON credit_ledger",
        "ALTER FUNCTION public.credit_ledger_guard() SECURITY DEFINER",
        "CREATE OR REPLACE FUNCTION public.credit_ledger_guard() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        "DROP TRIGGER credit_ledger_guard ON credit_ledger",
        "CREATE FUNCTION public.x() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    ],
)
def test_t96_the_app_role_cannot_lift_the_ledger_or_entitlement_guards(
    superuser: Connection, app: Engine, statement: str
) -> None:
    user = new_user(superuser)
    with pytest.raises(DBAPIError, match=r"permission denied|must be owner|must be superuser"):
        as_app(app, user, statement)


def test_t96_the_app_role_holds_no_other_role(superuser: Connection) -> None:
    held = (
        superuser.execute(
            text(
                "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
                "JOIN pg_roles a ON a.oid = m.member WHERE a.rolname = 'classbeat_app'"
            )
        )
        .scalars()
        .all()
    )
    assert held == []
