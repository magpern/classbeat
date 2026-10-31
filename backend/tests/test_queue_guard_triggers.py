"""The M5 guard triggers in the queue invariant (ADR 0005 section 3 as amended by ADR 0011 section
1; issue DB1, #207): the two trigger functions and triggers of migration 0007 are the only user
code admitted besides the queue functions, and only exactly. ``20-queue.sql`` (db-functions), the
startup check (``classbeat.db.queue_check``) and guard 9 each refuse a changed body, an extra
EXECUTE grant, an extra or changed trigger and a disabled one; guard 2 refuses the grant too."""

from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from classbeat.db.queue_check import (
    QUEUE_QUERY,
    guard_function_signatures,
    guard_trigger_signatures,
    queue_problems,
)
from dbtools import QUEUE_SCRIPT, install_queue, migration_module
from jobtools import engine_for
from queue_guard import check_queue
from role_guard import check_roles

# The current body (0008 replaced 0007's), as the migration creates it.
LEDGER_RESTORE = migration_module("ledger_guard_owner").LEDGER_GUARD


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def startup_problems(conn: Connection) -> list[str]:
    return queue_problems(conn.execute(text(QUEUE_QUERY)).one())


def script_refuses(database_url: str) -> bool:
    """True when ``20-queue.sql`` stops at its user-code check."""
    try:
        install_queue(database_url)
    except (DBAPIError, psycopg.Error) as error:
        return "besides the queue functions" in str(error)
    return False


def raw(conn: Connection, sql: str) -> None:
    """Plain SQL through the driver (no parameters, so a ``%`` in a function body stays)."""
    dbapi = conn.connection.dbapi_connection
    assert dbapi is not None
    cursor = dbapi.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def test_the_script_admits_exactly_the_pinned_strings() -> None:
    script = QUEUE_SCRIPT.read_text(encoding="utf-8")
    for signature in (*guard_function_signatures(), *guard_trigger_signatures()):
        assert f"'{signature}'" in script, signature


def test_the_pinned_guards_pass_all_three_checks(admin: Connection, database_url: str) -> None:
    assert startup_problems(admin) == []
    assert check_queue(admin) == []
    assert check_roles(admin) == []
    assert not script_refuses(database_url)


CHANGES = {
    "changed body": (
        LEDGER_RESTORE.replace(
            "RETURN NEW;\n        END IF;\n        -- The app",
            "RETURN NEW;\n        END IF;\n        RETURN NEW;\n        -- The app",
        ),
        LEDGER_RESTORE,
    ),
    "execute for the app": (
        "GRANT EXECUTE ON FUNCTION public.admin_audit_guard() TO classbeat_app",
        "REVOKE EXECUTE ON FUNCTION public.admin_audit_guard() FROM classbeat_app",
    ),
    "extra trigger": (
        "CREATE TRIGGER credit_ledger_guard_insert BEFORE INSERT ON public.credit_ledger "
        "FOR EACH ROW EXECUTE FUNCTION public.credit_ledger_guard()",
        "DROP TRIGGER credit_ledger_guard_insert ON public.credit_ledger",
    ),
    "disabled trigger": (
        "ALTER TABLE public.credit_ledger DISABLE TRIGGER credit_ledger_guard",
        "ALTER TABLE public.credit_ledger ENABLE TRIGGER credit_ledger_guard",
    ),
    "fewer events": (
        "DROP TRIGGER admin_audit_guard ON public.admin_audit; "
        "CREATE TRIGGER admin_audit_guard BEFORE UPDATE ON public.admin_audit "
        "FOR EACH ROW EXECUTE FUNCTION public.admin_audit_guard()",
        "DROP TRIGGER admin_audit_guard ON public.admin_audit; "
        "CREATE TRIGGER admin_audit_guard BEFORE UPDATE OR DELETE ON public.admin_audit "
        "FOR EACH ROW EXECUTE FUNCTION public.admin_audit_guard()",
    ),
    "with a condition": (
        "DROP TRIGGER credit_ledger_guard ON public.credit_ledger; "
        "CREATE TRIGGER credit_ledger_guard BEFORE UPDATE ON public.credit_ledger "
        "FOR EACH ROW WHEN (OLD.state = 'reserved') EXECUTE FUNCTION public.credit_ledger_guard()",
        "DROP TRIGGER credit_ledger_guard ON public.credit_ledger; "
        "CREATE TRIGGER credit_ledger_guard BEFORE UPDATE ON public.credit_ledger "
        "FOR EACH ROW EXECUTE FUNCTION public.credit_ledger_guard()",
    ),
    "another owner": (
        "ALTER FUNCTION public.credit_ledger_guard() OWNER TO postgres",
        "ALTER FUNCTION public.credit_ledger_guard() OWNER TO classbeat_owner",
    ),
}


@pytest.mark.parametrize(("change", "undo"), CHANGES.values(), ids=CHANGES.keys())
def test_any_change_to_a_guard_fails_every_check(
    admin: Connection, database_url: str, change: str, undo: str
) -> None:
    admin.execute(text("SELECT 1"))  # the connection is up before anything changes
    raw(admin, change)
    try:
        assert startup_problems(admin) != []
        assert check_queue(admin) != []
        assert script_refuses(database_url)
    finally:
        raw(admin, undo)
    assert startup_problems(admin) == []
    assert check_queue(admin) == []
    assert check_roles(admin) == []


MISSING = {
    "credit_ledger_guard": (
        "DROP TRIGGER credit_ledger_guard ON public.credit_ledger",
        "CREATE TRIGGER credit_ledger_guard BEFORE UPDATE ON public.credit_ledger "
        "FOR EACH ROW EXECUTE FUNCTION public.credit_ledger_guard()",
    ),
    "admin_audit_guard": (
        "DROP TRIGGER admin_audit_guard ON public.admin_audit",
        "CREATE TRIGGER admin_audit_guard BEFORE UPDATE OR DELETE ON public.admin_audit "
        "FOR EACH ROW EXECUTE FUNCTION public.admin_audit_guard()",
    ),
}


@pytest.mark.parametrize(("drop", "undo"), MISSING.values(), ids=MISSING.keys())
def test_a_missing_guard_trigger_fails_startup_and_guard_9(
    admin: Connection, drop: str, undo: str
) -> None:
    raw(admin, drop)
    try:
        assert any("M5 guard triggers" in p for p in startup_problems(admin))
        assert any("M5 guard triggers" in p for p in check_queue(admin))
    finally:
        raw(admin, undo)
    assert startup_problems(admin) == []
    assert check_queue(admin) == []


def test_the_role_guard_refuses_execute_on_a_guard_function(admin: Connection) -> None:
    for grantee in ("classbeat_app", "classbeat_queue", "PUBLIC"):
        admin.execute(text(f"GRANT EXECUTE ON FUNCTION public.credit_ledger_guard() TO {grantee}"))
        try:
            assert any("guard trigger function" in p or "PUBLIC" in p or "may execute" in p
                       for p in check_roles(admin)), grantee  # fmt: skip
        finally:
            admin.execute(
                text(f"REVOKE EXECUTE ON FUNCTION public.credit_ledger_guard() FROM {grantee}")
            )
    assert check_roles(admin) == []


def test_the_changed_body_case_changes_the_body() -> None:
    assert CHANGES["changed body"][0] != LEDGER_RESTORE
