"""Guard 6 of ADR 0002 section 6: web and worker refuse to run as a role that bypasses RLS."""

import logging
import secrets
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import text

from classbeat.config import Settings
from classbeat.db import DatabaseRoleError, create_engine, verify_database_role
from classbeat.db.role_check import PROBLEMS, problems_for
from classbeat.web import create_app
from conftest import url_for

PASSWORD = secrets.token_hex(8)
PROBE_ROLES = ("guard_super", "guard_bypass", "guard_member", "guard_table_owner", "guard_plain")


@pytest.fixture
def probe_roles(database_url: str) -> Iterator[None]:
    """Login roles that each break one rule, plus one that is fine."""
    engine = create_sync_engine(database_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        db = connection.execute(text("SELECT current_database()")).scalar_one()

        def run(*statements: str) -> None:
            for statement in statements:
                connection.execute(text(statement))

        def cleanup() -> None:
            run("DROP TABLE IF EXISTS t_probe_owned")
            for role in (*PROBE_ROLES, "guard_target"):
                exists = connection.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
                ).first()
                if exists:
                    run(f"DROP OWNED BY {role}", f"DROP ROLE {role}")

        cleanup()
        run(
            f"CREATE ROLE guard_super LOGIN SUPERUSER PASSWORD '{PASSWORD}'",
            f"CREATE ROLE guard_bypass LOGIN BYPASSRLS PASSWORD '{PASSWORD}'",
            "CREATE ROLE guard_target NOLOGIN BYPASSRLS",
            f"CREATE ROLE guard_member LOGIN NOINHERIT PASSWORD '{PASSWORD}'",
            "GRANT guard_target TO guard_member",
            f"CREATE ROLE guard_table_owner LOGIN PASSWORD '{PASSWORD}'",
            "CREATE TABLE t_probe_owned (id int)",
            "ALTER TABLE t_probe_owned OWNER TO guard_table_owner",
            f"CREATE ROLE guard_plain LOGIN PASSWORD '{PASSWORD}'",
        )
        for role in (
            "guard_super",
            "guard_bypass",
            "guard_member",
            "guard_table_owner",
            "guard_plain",
        ):
            run(f"GRANT CONNECT ON DATABASE {db} TO {role}")
        try:
            yield
        finally:
            cleanup()
    engine.dispose()


def settings_for(database_url: str, role: str, **overrides: object) -> Settings:
    url = url_for(database_url, role, PASSWORD)
    values: dict[str, object] = {"database_url": url, "environment": "test", **overrides}
    return Settings(**values)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("role", "problem"),
    [
        ("guard_super", "is a superuser"),
        ("guard_bypass", "has BYPASSRLS"),
        ("guard_member", "member of a superuser or BYPASSRLS role"),
        ("guard_table_owner", "owns tables"),
    ],
)
async def test_startup_and_every_connection_refuse_a_privileged_role(
    database_url: str, probe_roles: None, role: str, problem: str
) -> None:
    settings = settings_for(database_url, role)
    engine = create_engine(settings)
    try:
        with pytest.raises(DatabaseRoleError, match=problem) as startup:
            await verify_database_role(engine, settings)
        with pytest.raises(DatabaseRoleError, match=problem):
            async with engine.connect() as connection:  # the per-connection check
                await connection.execute(text("SELECT 1"))
        assert PASSWORD not in str(startup.value)
    finally:
        await engine.dispose()


async def test_a_role_that_is_fine_passes(database_url: str, probe_roles: None) -> None:
    settings = settings_for(database_url, "guard_plain")
    engine = create_engine(settings)
    try:
        await verify_database_role(engine, settings)
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()


async def test_the_app_role_passes(app_database_url: str) -> None:
    settings = Settings(database_url=app_database_url, environment="test")  # pyright: ignore[reportArgumentType]
    engine = create_engine(settings)
    try:
        await verify_database_role(engine, settings)
    finally:
        await engine.dispose()


async def test_the_owner_role_is_refused_for_web_and_worker(owner_database_url: str) -> None:
    settings = Settings(database_url=owner_database_url, environment="test")  # pyright: ignore[reportArgumentType]
    engine = create_engine(settings)
    try:
        with pytest.raises(DatabaseRoleError, match=r"owns tables|owns the database"):
            await verify_database_role(engine, settings)
    finally:
        await engine.dispose()


async def test_the_dev_exception_needs_dev_and_the_explicit_setting(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    # The test database URL is a superuser, as compose's postgres connection is until #4 lands.
    with caplog.at_level(logging.WARNING):
        settings = Settings(
            database_url=database_url,  # pyright: ignore[reportArgumentType]
            environment="dev",
            allow_privileged_database_role=True,
        )
        engine = create_engine(settings)
        try:
            await verify_database_role(engine, settings)
            async with engine.connect() as connection:
                assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
        finally:
            await engine.dispose()
    assert "privileged database role allowed" in caplog.text

    # Without the setting, dev refuses.
    refused = Settings(database_url=database_url, environment="dev")  # pyright: ignore[reportArgumentType]
    engine = create_engine(refused)
    try:
        with pytest.raises(DatabaseRoleError):
            await verify_database_role(engine, refused)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("environment", ["test", "prod"])
def test_the_exception_cannot_be_set_outside_dev(environment: str, database_url: str) -> None:
    with pytest.raises(ValueError, match="only allowed with environment=dev"):
        Settings(environment=environment, allow_privileged_database_role=True)  # pyright: ignore[reportArgumentType]


async def test_a_database_that_is_down_at_startup_does_not_stop_the_app_but_stays_checked(
    unreachable_database_url: str,
) -> None:
    settings = Settings(database_url=unreachable_database_url, environment="test")  # pyright: ignore[reportArgumentType]
    engine = create_engine(settings)
    try:
        await verify_database_role(engine, settings)  # no error: nothing to check yet
    finally:
        await engine.dispose()


async def test_the_web_app_does_not_start_with_a_privileged_role(database_url: str) -> None:
    settings = Settings(database_url=database_url, environment="test")  # pyright: ignore[reportArgumentType]
    app = create_app(settings)
    with pytest.raises(DatabaseRoleError):
        async with app.router.lifespan_context(app):
            pytest.fail("the app must not start")


async def test_the_web_app_starts_with_the_app_role(app_database_url: str) -> None:
    settings = Settings(database_url=app_database_url, environment="test")  # pyright: ignore[reportArgumentType]
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/ready")).status_code == 200


def test_every_problem_has_a_flag_and_the_messages_are_distinct() -> None:
    assert len(set(PROBLEMS)) == len(PROBLEMS) == 5
    assert problems_for((False,) * 5) == []
    assert problems_for((True, False, False, False, True)) == [PROBLEMS[0], PROBLEMS[4]]
