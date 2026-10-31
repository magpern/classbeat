"""``docker/postgres/init/10-roles.sql`` run again after migrating to head (#271, ADR 0011 note of
2026-10-31): its blanket re-grant of ``SELECT, INSERT, UPDATE, DELETE`` on every table is followed
by the M5 tables' own grants, so the app role ends with exactly what a plain migration leaves:
table and column privileges alike, on every table, and guard 2 (``check_roles``) passes.

Its own module: the re-run touches every table, which must not leak into other tests sharing the
migrated schema (``auth_schema`` migrates and drops it per module)."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from conftest import RoleUrls
from dbtools import tiers_migration
from jobtools import engine_for
from role_guard import check_roles

ROLES = Path(__file__).resolve().parents[2] / "docker" / "postgres" / "init" / "10-roles.sql"
REGRANT = "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classbeat_app;"


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def app_privileges(admin: Connection) -> set[tuple[str, str, str]]:
    """``(table, column or '*', privilege)`` the app role holds in schema public."""
    tables = admin.execute(
        text(
            "SELECT c.relname, '*', a.privilege_type FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a "
            "WHERE n.nspname = 'public' AND a.grantee = CAST('classbeat_app' AS regrole)"
        )
    ).all()
    columns = admin.execute(
        text(
            "SELECT c.relname, t.attname, a.privilege_type FROM pg_attribute t "
            "JOIN pg_class c ON c.oid = t.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace, "
            "aclexplode(t.attacl) a "
            "WHERE n.nspname = 'public' AND a.grantee = CAST('classbeat_app' AS regrole)"
        )
    ).all()
    return {(r[0], r[1], r[2]) for r in (*tables, *columns)}


def test_every_m5_table_is_restored_after_the_re_grant() -> None:
    script = ROLES.read_text("utf-8")
    regrant = script.index(REGRANT)
    for table, privileges in tiers_migration().APP_GRANTS.items():
        revoke = f"'REVOKE ALL ON TABLE public.{table} FROM classbeat_app'"
        assert script.count(revoke) == 1, table
        assert script.index(revoke) > regrant, table
        if privileges:
            grant = f"'GRANT {privileges} ON TABLE public.{table} TO classbeat_app'"
            assert script.count(grant) == 1, table
            assert script.index(grant) > script.index(revoke), table


def test_running_the_roles_file_again_keeps_the_migrated_privileges(
    admin: Connection, database_url: str, role_urls: RoleUrls, tmp_path: Path
) -> None:
    psql = shutil.which("psql")
    if psql is None:
        if os.environ.get("CI") == "true":
            pytest.fail("psql is needed to run 10-roles.sql in CI")
        pytest.skip("psql is not installed")
    before = app_privileges(admin)
    assert ("admin_audit", "*", "SELECT") not in before
    assert ("redeem_codes", "redeemed_count", "UPDATE") in before
    assert check_roles(admin) == []
    # The passwords the tests use (role_urls set random ones), so the re-run changes none.
    (tmp_path / "owner").write_text(make_url(role_urls.owner).password or "", "utf-8")
    (tmp_path / "app").write_text(make_url(role_urls.app).password or "", "utf-8")
    url = make_url(database_url)
    result = subprocess.run(  # noqa: S603 - our own script, fixed arguments
        [
            psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", url.host or "", "-p",
            str(url.port or 5432), "-U", url.username or "", "-d", url.database or "", "-f",
            str(ROLES),
        ],
        env={
            **os.environ,
            "DATABASE_OWNER_PASSWORD_FILE": str(tmp_path / "owner"),
            "DATABASE_PASSWORD_FILE": str(tmp_path / "app"),
            "PGPASSWORD": url.password or "",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert app_privileges(admin) == before
    assert check_roles(admin) == []
