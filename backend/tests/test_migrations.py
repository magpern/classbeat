from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

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
    assert script.get_heads() == ["0031_oauth_sign_up_purpose"]
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
        assert applied_revisions(database_url) == ["0031_oauth_sign_up_purpose"]
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
    # M3c (0009_models).
    "model_eval_results",
    "model_routes",
    # ADR 0016 (0018_swedebeat_mix_maps, 0020_swedebeat_analyses).
    "swedebeat_mix_maps",
    "swedebeat_analyses",
    "swedebeat_handoffs",
    # Issue #525 (0028_user_preferences).
    "user_preferences",
    # ADR 0007 amendment "beat-one corrections" (0029).
    "beat_one_corrections",
]


def test_0014_gives_each_plan_its_own_route_key_and_the_downgrade_restores_default(
    owner_database_url: str, database_url: str
) -> None:
    engine = create_engine(database_url)

    def keys() -> list[tuple[str, str]]:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT id, route_key FROM plans ORDER BY rank")).all()
        return [(r[0], r[1]) for r in rows]

    try:
        with migrated(owner_database_url, "0013_spotify_quota"):
            assert keys() == [("free", "default"), ("instructor", "default"), ("pro", "default")]
            command.upgrade(alembic_config(), "0014_plan_route_keys")
            assert keys() == [("free", "free"), ("instructor", "instructor"), ("pro", "pro")]
            command.downgrade(alembic_config(), "0013_spotify_quota")
            assert keys() == [("free", "default"), ("instructor", "default"), ("pro", "default")]
    finally:
        engine.dispose()


def test_0027_admits_the_soundless_flag_and_the_downgrade_deletes_its_row(
    owner_database_url: str,
) -> None:
    """#527: the ``app_settings.key`` check takes ``soundless_enabled`` after 0027, refuses it
    before, and the downgrade deletes a stored row first (as 0025 does) instead of failing."""

    engine = create_engine(owner_database_url)  # the app role only reads app_settings
    insert = text(
        "INSERT INTO app_settings (key, value) VALUES ('soundless_enabled', '\"on\"'::jsonb)"
    )

    def stored() -> list[str]:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT key FROM app_settings ORDER BY key")).all()
        return [r[0] for r in rows]

    try:
        with migrated(owner_database_url, "0026_plan_gates"):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert)
            command.upgrade(alembic_config(), "0027_soundless_flag")
            with engine.begin() as connection:
                connection.execute(insert)
                connection.execute(
                    text(
                        "INSERT INTO app_settings (key, value) "
                        "VALUES ('running_enabled', '\"off\"'::jsonb)"
                    )
                )
            assert stored() == ["running_enabled", "soundless_enabled"]
            command.downgrade(alembic_config(), "0026_plan_gates")
            assert stored() == ["running_enabled"]  # only the soundless row is gone
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert)
    finally:
        engine.dispose()


def test_0030_plan_price_privilege_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#510: at head the app role reads no price column of ``plans``; the downgrade gives back the
    table-level ``SELECT`` of migration 0007, and upgrading again takes it away."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)
    check = (
        "SELECT has_table_privilege('classbeat_app', 'public.plans', 'SELECT'), "
        "has_column_privilege('classbeat_app', 'public.plans', 'monthly_price_cents', 'SELECT'), "
        "has_column_privilege('classbeat_app', 'public.plans', 'currency', 'SELECT'), "
        "has_column_privilege('classbeat_app', 'public.plans', 'rank', 'SELECT')"
    )

    def held() -> tuple[bool, ...]:
        with admin.connect() as connection:
            return tuple(connection.execute(text(check)).one())

    try:
        command.upgrade(config, "head")
        assert held() == (False, False, False, True)
        command.downgrade(config, "0029_beat_one_corrections")
        assert held() == (True, True, True, True)
        command.upgrade(config, "head")
        assert held() == (False, False, False, True)
    finally:
        admin.dispose()
        command.downgrade(config, "base")


def test_0031_oauth_sign_up_purpose_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#617: ``oauth_flows.purpose`` takes ``sign_up`` at head; the downgrade deletes a waiting
    ``sign_up`` flow and refuses the value again, keeping the other flows."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)

    def insert(purpose: str, flow: str) -> None:
        with admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO oauth_flows (flow_hash, provider, purpose, state_hash, "
                    "pkce_verifier, expires_at) VALUES (:f, 'google', :p, 's', 'v', now())"
                ),
                {"f": flow.encode(), "p": purpose},
            )

    def purposes() -> list[str]:
        with admin.connect() as connection:
            rows = connection.execute(text("SELECT purpose FROM oauth_flows ORDER BY purpose"))
            return [row[0] for row in rows]

    try:
        command.upgrade(config, "head")
        insert("sign_in", "a")
        insert("sign_up", "b")
        with pytest.raises(IntegrityError):
            insert("sign_upp", "c")
        command.downgrade(config, "0030_plan_price_privilege")
        assert purposes() == ["sign_in"]
        with pytest.raises(IntegrityError):
            insert("sign_up", "d")
        command.upgrade(config, "head")
        insert("sign_up", "e")
        assert purposes() == ["sign_in", "sign_up"]
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM oauth_flows"))
        admin.dispose()
        command.downgrade(config, "base")
