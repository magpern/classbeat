import uuid
from pathlib import Path
from typing import Any

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
    assert script.get_heads() == ["0041_passkeys"]
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
        assert applied_revisions(database_url) == ["0041_passkeys"]
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
            assert tables() == {"alembic_version", *AUTH_0002}
            command.downgrade(alembic_config(), "0001_baseline")
            assert tables() == {"alembic_version"}
            command.upgrade(alembic_config(), "head")
            assert tables() == {"alembic_version", *AUTH, *CONNECTIONS, *JOBS}
            command.downgrade(alembic_config(), "0003_connections")
            assert tables() == {"alembic_version", *AUTH_0002, *CONNECTIONS}
            command.downgrade(alembic_config(), "0002_auth")
            assert tables() == {"alembic_version", *AUTH_0002}
            command.upgrade(alembic_config(), "head")
            assert tables() == {"alembic_version", *AUTH, *CONNECTIONS, *JOBS}
        assert tables() <= {"alembic_version"}
    finally:
        engine.dispose()


AUTH = sorted(metadata.tables)
PASSKEYS = ["webauthn_credentials", "webauthn_challenges"]  # 0041_passkeys (ADR 0019, #771)
AUTH_0002 = sorted(set(AUTH) - set(PASSKEYS))
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
    # Issue #632 (0034_security_log).
    "security_log",
    # ADR 0022 section 4, ME5 #656 (0037_mail_handoff).
    "mail_handoff",
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


def test_0032_analysis_slot_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#574: ``jobs.analysis_slot`` exists at head, false by default and never NULL; the
    downgrade drops it."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)

    def column() -> Any:
        with admin.connect() as connection:
            return connection.execute(
                text(
                    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_name = 'jobs' AND column_name = 'analysis_slot'"
                )
            ).first()

    try:
        command.upgrade(config, "head")
        found = column()
        assert found is not None
        assert tuple(found) == ("boolean", "NO", "false")
        command.downgrade(config, "0031_oauth_sign_up_purpose")
        assert column() is None
        command.upgrade(config, "head")
        assert column() is not None
    finally:
        admin.dispose()
        command.downgrade(config, "base")


def test_0033_session_client_mark_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#632: the session's client mark columns exist at head, nullable (an existing session has no
    mark); the downgrade drops them."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)

    def columns() -> list[tuple[str, str, str]]:
        with admin.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'sessions' AND column_name IN "
                    "('seen_ip_hash', 'seen_agent', 'location_changed_at', 'location_event_at') "
                    "ORDER BY column_name"
                )
            ).all()
        return [tuple(row) for row in rows]

    try:
        command.upgrade(config, "head")
        assert columns() == [
            ("location_changed_at", "timestamp with time zone", "YES"),
            ("location_event_at", "timestamp with time zone", "YES"),
            ("seen_agent", "text", "YES"),
            ("seen_ip_hash", "bytea", "YES"),
        ]
        command.downgrade(config, "0032_analysis_slot")
        assert columns() == []
        command.upgrade(config, "head")
        assert len(columns()) == 4
    finally:
        admin.dispose()
        command.downgrade(config, "base")


def test_0034_security_log_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#632: ``security_log`` exists at head with exactly its columns, owner RLS, and checks that
    refuse a raw path, a query string, an unknown method, a bad status or a short hash; the
    downgrade drops it."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)

    def columns() -> list[tuple[str, str, str]]:
        with admin.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'security_log' ORDER BY ordinal_position"
                )
            ).all()
        return [tuple(row) for row in rows]

    def insert(connection: Any, owner: Any, **values: Any) -> None:
        row = {
            "ip_hash": bytes(32),
            "method": "GET",
            "route": "/api/classes/{class_id}",
            "status": 200,
            "session": uuid.uuid4(),
            "minute": None,
            **values,
        }
        connection.execute(
            text(
                "INSERT INTO security_log (owner_id, session_id, ip_hash, method, route, status, "
                "limited_minute) VALUES (:o, :session, :ip_hash, :method, :route, :status, "
                ":minute)"
            ),
            {"o": owner, **row},
        )

    try:
        command.upgrade(config, "head")
        assert columns() == [
            ("id", "uuid", "NO"),
            ("owner_id", "uuid", "NO"),
            ("session_id", "uuid", "NO"),
            ("ip_hash", "bytea", "NO"),
            ("method", "text", "NO"),
            ("route", "text", "NO"),
            ("status", "smallint", "NO"),
            ("at", "timestamp with time zone", "NO"),
            ("limited_minute", "bigint", "YES"),
        ]
        with admin.begin() as connection:
            owner = connection.execute(
                text(
                    "INSERT INTO users (email, email_normalized, email_verified_at) "
                    "VALUES ('a@example.com', 'a@example.com', now()) RETURNING id"
                )
            ).scalar_one()
            insert(connection, owner)
            insert(connection, owner, route="unmatched", method="OTHER", status=429)
        refused = [
            {"route": "/api/classes/3f2b?token=abc"},
            {"route": "/api/classes/a%20b"},
            {"route": "/api/Classes"},
            {"route": "api/classes"},
            {"route": "/api/" + "a" * 200},
            {"method": "PROPFIND"},
            {"status": 99},
            {"status": 600},
            {"ip_hash": bytes(16)},
            {"minute": 1, "status": 200},  # only a 429 row carries its minute
        ]
        for values in refused:
            with admin.connect() as connection, pytest.raises(IntegrityError):
                insert(connection, owner, **values)
        # One 429 row per user and minute (the partial unique index), whatever the session;
        # another minute, another user or a non-429 row is not affected.
        session = uuid.uuid4()
        with admin.begin() as connection:
            other = connection.execute(
                text(
                    "INSERT INTO users (email, email_normalized, email_verified_at) "
                    "VALUES ('b@example.com', 'b@example.com', now()) RETURNING id"
                )
            ).scalar_one()
            insert(connection, owner, status=429, session=session, minute=7)
            insert(connection, owner, status=429, session=session, minute=8)
            insert(connection, other, status=429, minute=7)
            insert(connection, owner, session=session)
            insert(connection, owner, session=session)
        for duplicate in (session, uuid.uuid4()):  # same session or another one of the user's
            with admin.connect() as connection, pytest.raises(IntegrityError):
                insert(connection, owner, status=429, session=duplicate, minute=7)
        command.downgrade(config, "0033_session_client_mark")
        assert columns() == []
        command.upgrade(config, "head")
        assert len(columns()) == 9
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM users"))
        admin.dispose()
        command.downgrade(config, "base")


def test_0035_mail_settings_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#653: ``app_settings`` accepts the four mail keys at head and refuses an unknown key; the
    downgrade removes the mail rows and puts the old check back."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)

    def put(key: str) -> None:
        with admin.begin() as connection:
            connection.execute(
                text("INSERT INTO app_settings (key, value) VALUES (:k, '\"x\"'::jsonb)"),
                {"k": key},
            )

    def keys() -> list[str]:
        with admin.connect() as connection:
            rows = connection.execute(text("SELECT key FROM app_settings ORDER BY key")).all()
        return [row[0] for row in rows]

    try:
        command.upgrade(config, "head")
        for key in (
            "mail_sender_name",
            "mail_support_email",
            "mail_footer",
            "mail_kinds_disabled",
            "banner",
        ):
            put(key)
        with pytest.raises(IntegrityError):
            put("mail_other")
        command.downgrade(config, "0034_security_log")
        assert keys() == ["banner"]
        with pytest.raises(IntegrityError):
            put("mail_footer")
        command.upgrade(config, "head")
        put("mail_footer")
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM app_settings"))
        admin.dispose()
        command.downgrade(config, "base")


def test_0036_mail_preferences_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#655: existing users get the defaults (mail on), the check refuses an essential or unknown
    category, and the downgrade drops both columns."""
    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)

    def columns() -> set[str]:
        with admin.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'"
                )
            ).all()
        return {row[0] for row in rows}

    try:
        command.upgrade(config, "0035_mail_settings")
        with admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (email, email_normalized, email_verified_at) "
                    "VALUES ('old@example.test', 'old@example.test', now())"
                )
            )
        command.upgrade(config, "head")
        with admin.connect() as connection:
            row = connection.execute(
                text("SELECT mail_unsubscribed_all, mail_opted_out FROM users")
            ).one()
        assert row[0] is False
        assert row[1] == []
        for bad in ("security", "nonsense"):
            with pytest.raises(IntegrityError), admin.begin() as connection:
                connection.execute(text("UPDATE users SET mail_opted_out = ARRAY[:c]"), {"c": bad})
        command.downgrade(config, "0035_mail_settings")
        assert not columns() & {"mail_unsubscribed_all", "mail_opted_out"}
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM users"))
        admin.dispose()
        command.downgrade(config, "base")


def test_0037_mail_handoff_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#656: ``jobs.fresh_analysis`` defaults to false; ``mail_handoff`` takes one ``class_ready``
    row per job whose params hold the class id only, and goes with its job; the downgrade drops
    both."""
    from jobtools import new_job, new_user

    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)
    put = text(
        "INSERT INTO mail_handoff (owner_id, kind, job_id, params) "
        "VALUES (:o, :k, :j, CAST(:p AS jsonb))"
    )

    def has(table: str, column: str) -> bool:
        with admin.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = :c"
                    ),
                    {"t": table, "c": column},
                ).scalar_one()
            )

    try:
        command.upgrade(config, "head")
        with admin.begin() as connection:
            owner = new_user(connection)
            job = new_job(connection, owner)
            fresh = connection.execute(
                text("SELECT fresh_analysis FROM jobs WHERE id = :j"), {"j": job}
            ).scalar_one()
            assert fresh is False
            good = {"o": owner, "k": "class_ready", "j": job, "p": '{"class_id": "x"}'}
            connection.execute(put, good)
        with pytest.raises(IntegrityError), admin.begin() as connection:
            connection.execute(put, good)  # one row per job and kind
        bad_rows = [
            {**good, "k": "welcome"},
            {**good, "p": '{"class_id": "x", "email": "a@b.test"}'},
            {**good, "p": "{}"},
            {**good, "p": '{"class_id": 1}'},
            {**good, "p": '["class_id"]'},
        ]
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": job})
            left = connection.execute(text("SELECT count(*) FROM mail_handoff")).scalar_one()
            assert left == 0  # gone with its job
            job = new_job(connection, owner)
        for bad in bad_rows:
            with pytest.raises(IntegrityError), admin.begin() as connection:
                connection.execute(put, {**bad, "j": job})
        command.downgrade(config, "0036_mail_preferences")
        assert not has("mail_handoff", "id")
        assert not has("jobs", "fresh_analysis")
        command.upgrade(config, "head")
        assert has("mail_handoff", "params")
        assert has("jobs", "fresh_analysis")
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM users"))
        admin.dispose()
        command.downgrade(config, "base")


def test_0039_analysis_metrics_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#735: the three analysis timing columns of ``jobs`` are empty on a new job, are set
    together and never negative, and the downgrade drops them."""
    from jobtools import new_job, new_user

    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)
    names = ("analysis_fetch_ms", "analysis_decode_ms", "analysed_at")
    put = text(
        "UPDATE jobs SET analysis_fetch_ms = :f, analysis_decode_ms = :d, analysed_at = :at "
        "WHERE id = :j"
    )

    def columns() -> set[str]:
        with admin.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'jobs' AND column_name = ANY(:n)"
                ),
                {"n": list(names)},
            ).scalars()
            return set(rows)

    try:
        command.upgrade(config, "head")
        assert columns() == set(names)
        with admin.begin() as connection:
            job = new_job(connection, new_user(connection))
            row = connection.execute(
                text(f"SELECT {', '.join(names)} FROM jobs WHERE id = :j"),  # noqa: S608
                {"j": job},
            ).one()
            assert tuple(row) == (None, None, None)
            connection.execute(put, {"f": 1200, "d": 0, "at": "2026-11-07T10:00:00Z", "j": job})
        for bad in (
            {"f": -1, "d": 5, "at": "2026-11-07T10:00:00Z"},
            {"f": 5, "d": -1, "at": "2026-11-07T10:00:00Z"},
            {"f": 5, "d": 5, "at": None},
            {"f": None, "d": 5, "at": "2026-11-07T10:00:00Z"},
        ):
            with pytest.raises(IntegrityError), admin.begin() as connection:
                connection.execute(put, {**bad, "j": job})
        command.downgrade(config, "0038_locale")
        assert columns() == set()
        command.upgrade(config, "head")
        assert columns() == set(names)
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM users"))
        admin.dispose()
        command.downgrade(config, "base")


def test_0041_passkeys_up_and_down(
    owner_database_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0019 (#771): the passkey tables and ``users.webauthn_user_handle`` exist at head as
    system tables (no RLS) with the app role's DML; the downgrade drops them and the column, even
    with a passkey and a challenge stored."""
    from jobtools import new_user

    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    config = alembic_config()
    admin = create_engine(database_url)

    def state() -> tuple[set[str], bool]:
        with admin.connect() as connection:
            found = connection.execute(
                text(
                    "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                    "AND relkind = 'r' AND relname = ANY(:n) AND NOT relrowsecurity"
                ),
                {"n": PASSKEYS},
            ).scalars()
            handle = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = 'users' "
                    "AND column_name = 'webauthn_user_handle'"
                )
            ).first()
        return set(found), handle is not None

    try:
        command.upgrade(config, "head")
        assert state() == (set(PASSKEYS), True)
        with admin.begin() as connection:
            for table in PASSKEYS:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert connection.execute(
                        text("SELECT has_table_privilege('classbeat_app', :t, :p)"),
                        {"t": f"public.{table}", "p": privilege},
                    ).scalar_one(), (table, privilege)
            user = new_user(connection)
            connection.execute(
                text(
                    "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, "
                    "backup_eligible, backup_state, label) VALUES (:u, 'k', 'p', true, true, 'K')"
                ),
                {"u": user},
            )
            connection.execute(
                text(
                    "INSERT INTO webauthn_challenges (flow_hash, purpose, challenge, expires_at) "
                    "VALUES (sha256('f'), 'sign_in', sha256('c'), now())"
                )
            )
        command.downgrade(config, "0040_security_log_at_index")
        assert state() == (set(), False)
        command.upgrade(config, "head")
        assert state() == (set(PASSKEYS), True)
    finally:
        with admin.begin() as connection:
            connection.execute(text("DELETE FROM users"))
        admin.dispose()
        command.downgrade(config, "base")
