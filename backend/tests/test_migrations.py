from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from classbeat.auth.tables import metadata
from classbeat.db import SYSTEM_TABLES
from dbtools import install_queue, migrated
from rls_guard import check_tables
from role_guard import check_roles

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


def applied_revisions(database_url: str) -> list[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT version_num FROM alembic_version")).all()
    finally:
        engine.dispose()
    return [row[0] for row in rows]


def test_the_revisions_form_one_line() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0009_m5_hardening"]
    assert script.get_base() == "0001_baseline"


def test_offline_mode_renders_sql_without_a_database(capsys: pytest.CaptureFixture[str]) -> None:
    command.upgrade(alembic_config(), "head", sql=True)
    out = capsys.readouterr().out
    assert "CREATE TABLE alembic_version" in out
    assert "0001_baseline" in out
    assert "CREATE TABLE public.sessions" in out


def test_upgrade_head_then_downgrade_base(
    owner_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # env.py takes the owner role's URL from Settings, so the test points it at the test database.
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    database_url = owner_database_url
    config = alembic_config()
    try:
        command.upgrade(config, "head")
        assert applied_revisions(database_url) == ["0009_m5_hardening"]
        command.downgrade(config, "base")
        assert applied_revisions(database_url) == []
    finally:
        command.downgrade(config, "base")


def test_the_app_role_has_no_access_to_the_migration_table(
    owner_database_url: str,
    app_database_url: str,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    try:
        command.upgrade(config, "head")
        install_queue(database_url)  # compose's db-functions step
        app = create_engine(app_database_url)
        admin = create_engine(database_url)
        try:
            with app.connect() as connection:
                for statement in (
                    "SELECT * FROM alembic_version",
                    "UPDATE alembic_version SET version_num = 'x'",
                    "DELETE FROM alembic_version",
                ):
                    with pytest.raises(ProgrammingError, match="permission denied"):
                        connection.execute(text(statement))
                    connection.rollback()
            with admin.connect() as connection:
                assert check_roles(connection) == []
                assert check_tables(connection, SYSTEM_TABLES) == []
        finally:
            app.dispose()
            admin.dispose()
    finally:
        command.downgrade(config, "base")


def test_0002_auth_up_and_down(owner_database_url: str, database_url: str) -> None:
    engine = create_engine(database_url)

    def tables() -> set[str]:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            return {row[0] for row in rows}

    try:
        with migrated(owner_database_url, "0001_baseline"):
            assert tables() == {"alembic_version"}
            command.upgrade(alembic_config(), "0002_auth")
            assert tables() == {"alembic_version", *AUTH}
            command.downgrade(alembic_config(), "0001_baseline")
            assert tables() == {"alembic_version"}
            command.upgrade(alembic_config(), "head")
            assert tables() == {"alembic_version", *AUTH, *CONNECTIONS, *JOBS}
            command.downgrade(alembic_config(), "0003_connections")
            assert tables() == {"alembic_version", *AUTH, *CONNECTIONS}
            command.downgrade(alembic_config(), "0002_auth")
            assert tables() == {"alembic_version", *AUTH}
            command.upgrade(alembic_config(), "head")
            assert tables() == {"alembic_version", *AUTH, *CONNECTIONS, *JOBS}
        assert tables() <= {"alembic_version"}
    finally:
        engine.dispose()


AUTH = sorted(metadata.tables)
CONNECTIONS = ["connection_keks", "connections"]
JOBS = [
    "classes",
    "class_revisions",
    "jobs",
    "job_events",
    "external_operations",
    "mowl_playlist_links",
    "agent_runs",
    "class_messages",
    "saved_workouts",
    "spotify_oauth_flows",
    "spotify_pool",
    "class_playlists",
    # M5 (0007_tiers).
    "plans",
    "redeem_codes",
    "entitlements",
    "code_redemptions",
    "credit_ledger",
    "quota_periods",
    "app_settings",
    "admin_audit",
]
