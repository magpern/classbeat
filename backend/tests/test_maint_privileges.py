"""Guard 11 (ADR 0011 section 5): the maint role's attributes and exact privileges
(``maint_guard.check_maint``), proven to fail on a secret column grant, a table-level grant and a
new column, plus a source guard that only ``classbeat.admin.db`` touches the maint credentials."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from conftest import RoleUrls
from dbtools import migrated
from maint_guard import (
    HASH_READS,
    MAINT,
    SECRET_COLUMNS,
    check_maint,
    forbidden_calls,
    is_secret,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat"
MAINT_NAMES = frozenset(
    {
        "maint_sqlalchemy_url",
        "maint_database_url",
        "database_maint_password",
        "_maint_engine",
        "_maint_factory",
        "create_maint_engine",
    }
)
# Where those names may appear: the settings that define them and the module that uses them.
MAINT_MODULES = frozenset({SRC / "config.py", SRC / "admin" / "db.py"})


@pytest.fixture(scope="module")
def schema(role_urls: RoleUrls) -> Iterator[None]:
    with migrated(role_urls.owner):
        yield


@pytest.fixture
def admin(schema: None, database_url: str) -> Iterator[Connection]:
    """A superuser connection whose changes are rolled back after the test."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_the_installed_role_passes(admin: Connection) -> None:
    assert check_maint(admin) == []


def test_maint_cannot_read_a_secret_column(admin: Connection) -> None:
    for table, column in (("users", "password_hash"), ("connections", "secret")):
        found = admin.execute(
            text("SELECT has_column_privilege(:r, CAST(:t AS regclass), :c, 'SELECT')"),
            {"r": MAINT, "t": f"public.{table}", "c": column},
        ).scalar_one()
        assert found is False, f"{table}.{column}"


def test_maint_reads_the_plan_prices_the_app_cannot(admin: Connection) -> None:
    """#510: the prices are readable by the admin role only (the app role's grant is per column
    without them, migration 0030; guard 2)."""
    for column in ("monthly_price_cents", "intro_price_cents", "intro_price_until", "currency"):
        held = admin.execute(
            text(
                "SELECT has_column_privilege(:r, 'public.plans', :c, 'SELECT'), "
                "has_column_privilege('classbeat_app', 'public.plans', :c, 'SELECT')"
            ),
            {"r": MAINT, "c": column},
        ).one()
        assert tuple(held) == (True, False), column


def test_a_secret_column_grant_fails(admin: Connection) -> None:
    admin.execute(text(f"GRANT SELECT (password_hash) ON users TO {MAINT}"))
    problems = check_maint(admin)
    assert "users.password_hash: classbeat_maint can read a secret column" in problems


def test_a_table_level_grant_fails(admin: Connection) -> None:
    admin.execute(text(f"GRANT SELECT ON connections TO {MAINT}"))
    problems = check_maint(admin)
    assert "connections.secret: classbeat_maint can read a secret column" in problems
    assert any(p.startswith("connections: classbeat_maint SELECT must be") for p in problems)


def test_a_new_readable_column_needs_a_decision(admin: Connection) -> None:
    # A table-level grant covers a column added later; a column grant does not, so a new
    # column of plans (all columns granted one by one) stays unreadable until decided.
    admin.execute(text("ALTER TABLE app_settings ADD COLUMN extra text"))
    admin.execute(text(f"GRANT SELECT ON app_settings TO {MAINT}"))
    problems = check_maint(admin)
    assert "app_settings.extra: classbeat_maint can read a column no registry decision covers" in (
        problems
    )


def test_maint_reads_the_security_log_columns_of_the_abuse_view_only(admin: Connection) -> None:
    """#632: five columns, the keyed network hash among them; never the method or route."""
    held = {
        column: admin.execute(
            text("SELECT has_column_privilege(:r, 'public.security_log', :c, 'SELECT')"),
            {"r": MAINT, "c": column},
        ).scalar_one()
        for column in ("owner_id", "session_id", "ip_hash", "status", "at", "id", "method",
                       "route", "limited_minute")
    }  # fmt: skip
    assert {c for c, ok in held.items() if ok} == {"owner_id", "session_id", "ip_hash", "status",
                                                   "at"}  # fmt: skip
    writes = admin.execute(
        text(
            "SELECT p FROM unnest(ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) AS p "
            "WHERE has_table_privilege(:r, 'public.security_log', p)"
        ),
        {"r": MAINT},
    ).all()
    assert writes == []


def test_only_the_security_log_network_hash_is_an_allowed_hash_read(admin: Connection) -> None:
    assert {("security_log", "ip_hash")} == HASH_READS
    assert not is_secret("security_log", "ip_hash")
    assert is_secret("admin_audit", "ip_hash")  # the audit log's stays secret
    assert is_secret("sessions", "token_hash")
    assert is_secret("security_log", "session_hash")
    # Reading the hash is allowed only on that table: the same grant elsewhere fails.
    admin.execute(text(f"GRANT SELECT (ip_hash) ON admin_audit TO {MAINT}"))
    assert "admin_audit.ip_hash: classbeat_maint can read a secret column" in check_maint(admin)


def test_the_hash_exception_is_for_the_security_log_table_only(admin: Connection) -> None:
    """A column named ``ip_hash`` on a table that is not in ``SECRET_COLUMNS`` is still secret
    (a table-blind exception would let it through) and a grant on it is reported."""
    assert "ip_hash" not in SECRET_COLUMNS.get("plans", frozenset())
    assert is_secret("plans", "ip_hash")
    admin.execute(text("ALTER TABLE plans ADD COLUMN ip_hash bytea"))
    admin.execute(text(f"GRANT SELECT (ip_hash) ON plans TO {MAINT}"))
    assert "plans.ip_hash: classbeat_maint can read a secret column" in check_maint(admin)


def test_a_new_security_log_column_needs_a_decision(admin: Connection) -> None:
    admin.execute(text("ALTER TABLE security_log ADD COLUMN extra text"))
    admin.execute(text(f"GRANT SELECT (extra) ON security_log TO {MAINT}"))
    assert "security_log.extra: classbeat_maint can read a column no registry decision covers" in (
        check_maint(admin)
    )


def test_a_secret_looking_new_column_is_secret(admin: Connection) -> None:
    admin.execute(text("ALTER TABLE plans ADD COLUMN api_token text"))
    admin.execute(text(f"GRANT SELECT (api_token) ON plans TO {MAINT}"))
    assert "plans.api_token: classbeat_maint can read a secret column" in check_maint(admin)
    assert is_secret("anything", "reset_token")
    assert is_secret("anything", "state_hash")
    assert not is_secret("users", "email")


def test_role_drift_fails(admin: Connection) -> None:
    admin.execute(text(f"ALTER ROLE {MAINT} INHERIT"))
    admin.execute(text(f"ALTER ROLE {MAINT} SET default_transaction_read_only = off"))
    admin.execute(text(f"GRANT classbeat_app TO {MAINT}"))
    admin.execute(text("CREATE SCHEMA maint_guard_probe"))
    admin.execute(text(f"GRANT USAGE ON SCHEMA maint_guard_probe TO {MAINT}"))
    admin.execute(text(f"GRANT EXECUTE ON FUNCTION pg_catalog.pg_sleep(float8) TO {MAINT}"))
    problems = "\n".join(check_maint(admin))
    assert "NOINHERIT" in problems
    assert "role settings must be exactly" in problems
    assert "member of no role" in problems
    assert "schema:maint_guard_probe:USAGE" in problems
    assert "function:pg_sleep(double precision):EXECUTE" in problems


def test_secret_registry_has_no_stale_entry(admin: Connection) -> None:
    present = {
        (t, c)
        for t, c in admin.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        )
    }
    tables = {t for t, _ in present}
    for table, columns in SECRET_COLUMNS.items():
        assert table in tables, f"SECRET_COLUMNS names a table that is gone: {table}"
        for column in columns:
            assert (table, column) in present, f"{table}.{column} is gone"


def test_only_the_admin_db_module_touches_maint_credentials() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in MAINT_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.id
                if isinstance(node, ast.Name)
                else node.name
                if isinstance(node, ast.alias)
                else node.value
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                else None
            )
            if isinstance(name, str) and name.rsplit(".", 1)[-1] in MAINT_NAMES:
                offenders.append(f"{path.relative_to(SRC)}:{getattr(node, 'lineno', '?')}")
    assert offenders == []


def test_the_admin_package_never_notifies_or_takes_advisory_locks() -> None:
    found = [
        f"{path.relative_to(SRC)}:{line}: {what}"
        for path in sorted((SRC / "admin").rglob("*.py"))
        for line, what in forbidden_calls(path.read_text(encoding="utf-8"))
    ]
    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "q = text(\"SELECT pg_notify('jobs_queued', '')\")",
        "q = text('NOTIFY jobs_queued')",
        "q = text('LISTEN job_events')",
        "q = text('SELECT pg_advisory_xact_lock(1)')",
        "q = text('SELECT pg_try_advisory_lock(1)')",
        "q = select(func.pg_advisory_lock(1))",
        "q = select(sqlalchemy.func.pg_notify('x', 'y'))",
        "from sqlalchemy.func import pg_advisory_unlock_all",
    ],
)
def test_the_notify_and_advisory_check_fails_on_a_use(source: str) -> None:
    assert forbidden_calls(source) != []


def test_the_notify_check_ignores_docstrings() -> None:
    source = '"""We never NOTIFY or listen here."""\nx = 1\n'
    assert forbidden_calls(source) == []
