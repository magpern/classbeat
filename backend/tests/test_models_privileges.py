"""``docker/postgres/init/10-roles.sql`` run again after migrating to head (ADR 0012 section 5;
#211): its re-grant of ``SELECT, INSERT, UPDATE, DELETE`` on every table is followed by the M3c
line, so the app role keeps exactly ``SELECT, INSERT`` on the eval results and ``SELECT, INSERT,
UPDATE, DELETE`` (no ``TRUNCATE``) on the routes.

Its own module: the re-run re-grants every table, which would leak into the other tests of a
module sharing the migrated schema (``auth_schema`` migrates and drops it per module)."""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from conftest import RoleUrls
from jobtools import engine_for

ROLES = Path(__file__).resolve().parents[2] / "docker" / "postgres" / "init" / "10-roles.sql"
KINDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
M3C_LINE = (
    "SELECT 'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.model_eval_results "
    "FROM classbeat_app'\n"
    " WHERE to_regclass('public.model_eval_results') IS NOT NULL \\gexec\n"
)


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def privileges(admin: Connection, table: str) -> set[str]:
    return {
        kind
        for kind in KINDS
        if admin.execute(
            text("SELECT has_table_privilege('classbeat_app', :t, :p)"),
            {"t": f"public.{table}", "p": kind},
        ).scalar_one()
    }


def test_the_m3c_revoke_comes_after_the_re_grant() -> None:
    script = ROLES.read_text("utf-8")
    regrant = script.index(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO classbeat_app;"
    )
    assert script.count(M3C_LINE) == 1
    assert script.index(M3C_LINE) > regrant
    # Next to the other per-table revokes (connection_keks, class_messages, agent_runs).
    assert script.index(M3C_LINE) > script.index("public.agent_runs FROM classbeat_app")


def test_running_the_roles_file_again_keeps_results_append_only(
    admin: Connection, database_url: str, role_urls: RoleUrls, tmp_path: Path
) -> None:
    psql = shutil.which("psql")
    if psql is None:
        if os.environ.get("CI") == "true":
            pytest.fail("psql is needed to run 10-roles.sql in CI")
        pytest.skip("psql is not installed")
    before = {t: privileges(admin, t) for t in ("model_eval_results", "model_routes")}
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
    after = {t: privileges(admin, t) for t in ("model_eval_results", "model_routes")}
    assert (
        after
        == before
        == {
            "model_eval_results": {"SELECT", "INSERT"},
            "model_routes": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        }
    )
