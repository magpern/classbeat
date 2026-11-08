"""``docker/postgres/init/10-roles.sql`` run on a database migrated to a revision *below* 0030
(#606, #610): the script grants ``plans`` column by column for the columns that exist, so an
older database ends with exactly the app columns it has and never a price, and never the
table-level ``SELECT`` migration 0007 gave. (``test_roles_rerun.py`` covers a re-run at head.)

Its own module: ``migrated`` drops the schema to base, which the module-scoped ``auth_schema`` of
the re-run tests must not see."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from conftest import RoleUrls
from dbtools import migrated, migration_module

ROLES = Path(__file__).resolve().parents[2] / "docker" / "postgres" / "init" / "10-roles.sql"
GATE_COLUMNS = {"max_class_minutes", "games_allowed", "races_allowed", "ftp_tests_allowed"}


def run_roles_file(database_url: str, role_urls: RoleUrls, tmp_path: Path) -> None:
    psql = shutil.which("psql")
    if psql is None:
        if os.environ.get("CI") == "true":
            pytest.fail("psql is needed to run 10-roles.sql in CI")
        pytest.skip("psql is not installed")
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


@pytest.mark.parametrize(
    ("revision", "has_gates"),
    [("0029_beat_one_corrections", True), ("0025_running_flag", False)],
    ids=["0029", "0025-before-the-gate-columns"],
)
def test_the_roles_file_on_a_database_below_0030_grants_the_columns_it_has_and_no_price(
    owner_database_url: str,
    database_url: str,
    role_urls: RoleUrls,
    tmp_path: Path,
    revision: str,
    has_gates: bool,
) -> None:
    migration = migration_module("plan_price_privilege")
    admin = create_engine(database_url)
    try:
        with migrated(owner_database_url, revision):
            with admin.connect() as conn:  # 0007's table-level SELECT is what a plain DB holds
                assert conn.execute(
                    text("SELECT has_table_privilege('classbeat_app', 'public.plans', 'SELECT')")
                ).scalar_one()
            run_roles_file(database_url, role_urls, tmp_path)
            with admin.connect() as conn:
                existing = set(
                    conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'public' AND table_name = 'plans'"
                        )
                    ).scalars()
                )
                readable = {
                    column
                    for column in existing
                    if conn.execute(
                        text(
                            "SELECT has_column_privilege("
                            "'classbeat_app', 'public.plans', :c, 'SELECT')"
                        ),
                        {"c": column},
                    ).scalar_one()
                }
                table_level = conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a "
                        "WHERE c.oid = 'public.plans'::regclass AND a.privilege_type = 'SELECT' "
                        "AND a.grantee = CAST('classbeat_app' AS regrole)"
                    )
                ).scalar_one()
    finally:
        admin.dispose()
    assert has_gates == bool(GATE_COLUMNS & existing)  # the revision is the one the case names
    assert table_level == 0
    assert readable == set(migration.APP_COLUMNS) & existing
    assert not readable & set(migration.PRICE_COLUMNS)
    if has_gates:  # the prices arrived with the gate columns (0026): there to be left out
        assert set(migration.PRICE_COLUMNS) <= existing
