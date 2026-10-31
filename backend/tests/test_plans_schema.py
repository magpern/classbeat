"""Migration 0007 (ADR 0011 section 1; issue DB1, #207): the M5 tables, the app role's grants,
the restrictive policies, the ledger and audit triggers and the cascades. Guards 1, 2 and 10 run
on the migrated schema. The ledger and audit triggers are in ``test_plans_ledger.py``, the
backfill in ``test_plans_backfill.py``, the effective plan in ``test_plans_resolve.py``."""

import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from classbeat.db import RETENTION, SYSTEM_TABLES
from classbeat.plans import tables
from jobtools import as_user, digest, engine_for, new_user
from retention_guard import check_retention
from rls_guard import check_tables
from role_guard import check_roles

OWNED = ("entitlements", "code_redemptions", "credit_ledger", "quota_periods")
SYSTEM = ("plans", "redeem_codes", "app_settings", "admin_audit")
MAINT = "classbeat_maint"
REFUSED = "change refused"


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url) as engine:
        yield engine


def ledger(conn: Connection, owner: uuid.UUID, job_id: uuid.UUID | None, **c: Any) -> uuid.UUID:
    """A ledger row written as the superuser (setup only, so any state)."""
    values = {"owner_id": owner, "job_id": job_id, "plan_id": "free", "state": "reserved", **c}
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    return conn.execute(
        text(
            f"INSERT INTO credit_ledger (period, {names}) "  # noqa: S608
            f"VALUES (CAST(date_trunc('month', now()) AS date), {params}) RETURNING id"
        ),
        values,
    ).scalar_one()


def row(conn: Connection, ledger_id: uuid.UUID) -> Any:
    return conn.execute(text("SELECT * FROM credit_ledger WHERE id = :id"), {"id": ledger_id}).one()


def code(conn: Connection, **c: Any) -> uuid.UUID:
    values = {
        "code_hash": digest(secrets.token_hex(8)), "label": "beta", "plan_id": "instructor",
        "grant_days": 30, "max_redemptions": 5, **c,
    }  # fmt: skip
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    return conn.execute(
        text(
            f"INSERT INTO redeem_codes ({names}, expires_at) "  # noqa: S608
            f"VALUES ({params}, now() + interval '30 days') RETURNING id"
        ),
        values,
    ).scalar_one()


@contextmanager
def as_owner_of(engine: Engine, user: uuid.UUID) -> Iterator[Connection]:
    with engine.begin() as connection:
        as_user(connection, user)
        yield connection


def insert_sql(table: str, values: dict[str, Any]) -> Any:
    """``INSERT INTO table (names) VALUES (:names)``; the names are this module's constants."""
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    return text(f"INSERT INTO {table} ({names}) VALUES ({params})")  # noqa: S608


def update_as(engine: Engine, user: uuid.UUID, statement: str, **params: Any) -> int:
    with as_owner_of(engine, user) as connection:
        return connection.execute(text(statement), params).rowcount


# Guards and grants.


def test_the_guards_hold_on_the_migrated_schema(admin: Connection) -> None:
    for table in OWNED:
        assert table not in SYSTEM_TABLES
        assert table in RETENTION
    for table in SYSTEM:
        assert SYSTEM_TABLES[table].strip()
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_roles(admin) == []
    assert check_retention(admin, RETENTION, SYSTEM_TABLES) == []


EXPECTED_GRANTS = {
    "plans": {"SELECT"},
    "app_settings": {"SELECT"},
    "entitlements": {"SELECT", "INSERT"},
    "code_redemptions": {"SELECT", "INSERT"},
    "redeem_codes": {"SELECT"},
    "credit_ledger": {"SELECT", "INSERT", "UPDATE"},
    "quota_periods": {"SELECT", "INSERT", "UPDATE"},
    "admin_audit": set(),
}
ALL_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def test_the_app_role_has_exactly_the_adr_grants(admin: Connection) -> None:
    for table, expected in EXPECTED_GRANTS.items():
        held = {
            p
            for p in ALL_PRIVILEGES
            if admin.execute(
                text("SELECT has_table_privilege('classbeat_app', :t, :p)"),
                {"t": f"public.{table}", "p": p},
            ).scalar_one()
        }
        assert held == expected, table
    # Column privileges: only ``redeemed_count`` of a redeem code is writable.
    for column in ("redeemed_count", "label", "max_redemptions", "expires_at", "disabled_at"):
        writable = admin.execute(
            text(
                "SELECT has_column_privilege('classbeat_app', 'public.redeem_codes', :c, 'UPDATE')"
            ),
            {"c": column},
        ).scalar_one()
        assert writable == (column == "redeemed_count"), column
    assert not admin.execute(
        text("SELECT has_any_column_privilege('classbeat_app', 'public.admin_audit', 'SELECT')")
    ).scalar_one()


def test_the_role_guard_fails_on_a_new_column_grant_or_a_wider_table_grant(
    admin: Connection,
) -> None:
    for grant, undo in (
        (
            "GRANT UPDATE (max_redemptions) ON public.redeem_codes TO classbeat_app",
            "REVOKE UPDATE (max_redemptions) ON public.redeem_codes FROM classbeat_app",
        ),
        (
            "GRANT UPDATE ON public.entitlements TO classbeat_app",
            "REVOKE UPDATE ON public.entitlements FROM classbeat_app",
        ),
        (
            "GRANT SELECT ON public.admin_audit TO classbeat_app",
            "REVOKE SELECT ON public.admin_audit FROM classbeat_app",
        ),
        (
            "REVOKE UPDATE (redeemed_count) ON public.redeem_codes FROM classbeat_app",
            "GRANT UPDATE (redeemed_count) ON public.redeem_codes TO classbeat_app",
        ),
    ):
        admin.execute(text(grant))
        try:
            assert check_roles(admin) != [], grant
        finally:
            admin.execute(text(undo))
    assert check_roles(admin) == []


def test_the_rls_guard_requires_the_ledger_and_entitlement_policies(
    admin: Connection,
) -> None:
    for policy, table in (
        ("credit_ledger_no_delete", "credit_ledger"),
        ("credit_ledger_insert_reserved", "credit_ledger"),
        ("credit_ledger_settle", "credit_ledger"),
        ("entitlements_app_sources", "entitlements"),
    ):
        definition = admin.execute(
            text(
                "SELECT cmd, qual, with_check FROM pg_policies "
                "WHERE tablename = :t AND policyname = :p"
            ),
            {"t": table, "p": policy},
        ).one()
        admin.execute(text("BEGIN"))
        try:
            admin.execute(text(f"DROP POLICY {policy} ON public.{table}"))
            found = check_tables(admin, SYSTEM_TABLES)
            assert any(p.startswith(f"{table}: needs a restrictive FOR") for p in found), (
                policy,
                definition,
                found,
            )
        finally:
            admin.execute(text("ROLLBACK"))
    assert check_tables(admin, SYSTEM_TABLES) == []


def test_the_query_mirror_matches_the_schema(admin: Connection) -> None:
    found = inspect(admin)
    for table in tables.metadata.tables.values():
        columns = {c["name"] for c in found.get_columns(table.name)}
        assert columns == {c.name for c in table.columns}, table.name


def test_the_plans_are_seeded_with_the_owner_defaults(admin: Connection) -> None:
    rows = admin.execute(
        text(
            "SELECT id, rank, classes_per_month, revisions_per_class, concurrent_jobs, "
            "monthly_cost_micros, route_key, features FROM plans ORDER BY rank"
        )
    ).all()
    assert [tuple(r) for r in rows] == [
        ("free", 0, 3, 3, 1, 10_000_000, "default", []),
        ("instructor", 10, 30, 10, 2, 60_000_000, "default", []),
        ("pro", 20, 100, 25, 3, 200_000_000, "default", []),
    ]


# Entitlements.


def entitle(engine: Engine, user: uuid.UUID, source: str, **c: Any) -> None:
    values = {"owner_id": user, "plan_id": "instructor", "source": source, **c}
    with as_owner_of(engine, user) as connection:
        connection.execute(insert_sql("entitlements", values), values)


def test_the_app_role_adds_only_code_and_trial_entitlements(admin: Connection, app: Engine) -> None:
    user = new_user(admin)
    for source in ("grant", "subscription"):
        with pytest.raises(DBAPIError, match="row-level security"):
            entitle(app, user, source)
    # Migration 0009: the app's rows always end (tests/test_plans_hardening.py has the limits).
    soon = datetime.now(UTC) + timedelta(days=30)
    entitle(app, user, "trial", ends_at=soon)
    entitle(app, user, "code", code_id=code(admin), ends_at=soon)
    with pytest.raises(IntegrityError, match="entitlements_code"):
        entitle(app, user, "code", ends_at=soon)
    with pytest.raises(IntegrityError, match="entitlements_one_trial"):
        entitle(app, user, "trial", ends_at=soon)
    for statement in (
        "UPDATE entitlements SET ends_at = NULL",
        "UPDATE entitlements SET revoked_at = NULL",
        "DELETE FROM entitlements",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            update_as(app, user, statement)


def test_entitlement_checks(admin: Connection) -> None:
    user = new_user(admin)
    for columns, constraint in (
        ({"plan_id": None}, "entitlements_grants_something"),
        ({"features": ["races"]}, "entitlements_features"),
        ({"features": [None]}, "entitlements_features"),
        ({"note": "x" * 201}, "entitlements_note"),
    ):
        values = {"owner_id": user, "plan_id": "pro", "source": "grant", **columns}
        with pytest.raises(IntegrityError, match=constraint):
            admin.execute(insert_sql("entitlements", values), values)
    with pytest.raises(IntegrityError, match="entitlements_period"):
        admin.execute(
            text(
                "INSERT INTO entitlements (owner_id, plan_id, source, starts_at, ends_at) "
                "VALUES (:o, 'pro', 'grant', now(), now() - interval '1 day')"
            ),
            {"o": user},
        )


def test_the_app_role_can_only_count_a_redemption(admin: Connection, app: Engine) -> None:
    user, code_id = new_user(admin), code(admin, max_redemptions=1)
    assert (
        update_as(app, user, "UPDATE redeem_codes SET redeemed_count = 1 WHERE id = :i", i=code_id)
        == 1
    )
    # Used up: migration 0009's trigger refuses it before the check constraint would.
    with pytest.raises(DBAPIError, match=REFUSED):
        update_as(app, user, "UPDATE redeem_codes SET redeemed_count = 2 WHERE id = :i", i=code_id)
    for statement in (
        "UPDATE redeem_codes SET max_redemptions = 1000",
        "UPDATE redeem_codes SET disabled_at = NULL",
        "DELETE FROM redeem_codes",
        "UPDATE plans SET classes_per_month = 10000",
        "INSERT INTO app_settings (key, value) VALUES ('trial', '{}')",
        "INSERT INTO admin_audit (actor_email, action, outcome, request_id, ip_hash) "
        "VALUES ('a@example.test', 'x', 'ok', 'r', '\\x00')",
        "SELECT * FROM admin_audit",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            update_as(app, user, statement)
