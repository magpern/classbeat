"""Guard 9 and the startup and per-connection queue check (ADR 0005 section 3).

Every drift case below is planted as the superuser, must be found by guard 9 and refused by the
check web and worker run, and is then repaired by running ``20-queue.sql`` again (so the cases
also prove the script repairs what it manages).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import APP_SECRET
from classbeat.config import Settings
from classbeat.db import DatabaseRoleError, create_engine, queue_verified, verify_database_role
from classbeat.db.queue_check import QUEUE_QUERY, queue_problems
from classbeat.web import create_app
from classbeat.worker.__main__ import _run  # pyright: ignore[reportPrivateUsage]
from dbtools import QUEUE_SCRIPT, install_queue
from jobtools import engine_for
from queue_guard import check_queue
from role_guard import check_roles

CLAIM_SIG = "public.queue_claim(text,integer,text[])"


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def app_settings(app_database_url: str, **values: object) -> Settings:
    return Settings(database_url=app_database_url, environment="test", **values)  # pyright: ignore[reportArgumentType]


async def startup_and_connection_problems(app_database_url: str) -> tuple[str, str]:
    """The messages of the startup check and of the per-connection check ('' when they pass)."""
    messages: list[str] = []
    engine = create_engine(app_settings(app_database_url))
    try:
        try:
            await verify_database_role(engine)
            messages.append("")
        except DatabaseRoleError as exc:
            messages.append(str(exc))
        await engine.dispose()  # the next connection is a new one
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            messages.append("")
        except DatabaseRoleError as exc:
            messages.append(str(exc))
    finally:
        await engine.dispose()
    return messages[0], messages[1]


async def test_guard_9_and_the_startup_check_pass_on_the_installed_queue(
    admin: Connection, app_database_url: str
) -> None:
    assert check_queue(admin) == []
    assert check_roles(admin) == []
    assert await startup_and_connection_problems(app_database_url) == ("", "")


def test_the_script_is_plain_sql_and_refuses_a_non_superuser(owner_database_url: str) -> None:
    script = QUEUE_SCRIPT.read_text(encoding="utf-8")
    assert not [line for line in script.splitlines() if line.startswith("\\")]
    # psql --single-transaction makes the one transaction.
    assert "BEGIN;" not in script
    assert "COMMIT;" not in script
    with pytest.raises(Exception, match="must run as the superuser"):
        install_queue(owner_database_url)


def test_the_script_is_idempotent(admin: Connection, database_url: str) -> None:
    install_queue(database_url)
    install_queue(database_url)
    assert check_queue(admin) == []


DRIFT = [
    ("GRANT EXECUTE ON FUNCTION public.queue_stats() TO PUBLIC", "queue_stats"),
    ("GRANT EXECUTE ON FUNCTION public.queue_stats() TO classbeat_owner", "queue_stats"),
    (f"REVOKE EXECUTE ON FUNCTION {CLAIM_SIG} FROM classbeat_queue", "queue_claim"),
    ("ALTER FUNCTION public.queue_reap(integer) SECURITY INVOKER", "queue_reap"),
    (f"ALTER FUNCTION {CLAIM_SIG} SET search_path = public, pg_temp", "queue_claim"),
    ("COMMENT ON FUNCTION public.queue_stats() IS 'classbeat-queue v0'", "queue_stats"),
    ("ALTER FUNCTION public.queue_expire(integer) OWNER TO classbeat_owner", "queue_expire"),
    (
        "CREATE OR REPLACE FUNCTION public.queue_stats() RETURNS TABLE (metric text, total bigint)"
        " LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp"
        " AS $$ BEGIN RETURN QUERY SELECT 'x', 0::bigint; END $$",
        "queue_stats",
    ),
    (
        "CREATE FUNCTION public.queue_stats(p integer) RETURNS integer LANGUAGE sql AS 'SELECT 1'",
        "queue_stats",
    ),
    ("GRANT DELETE ON public.jobs TO classbeat_queue", "classbeat_queue"),
    ("REVOKE UPDATE ON public.jobs FROM classbeat_queue", "classbeat_queue"),
    ("GRANT SELECT ON public.connections TO classbeat_queue", "classbeat_queue"),
    ("GRANT SELECT (title) ON public.classes TO classbeat_queue", "classbeat_queue"),
    ("GRANT CREATE ON SCHEMA public TO classbeat_queue", "classbeat_queue"),
    ("GRANT CONNECT ON DATABASE postgres TO classbeat_queue", "classbeat_queue"),
    ("GRANT pg_read_all_data TO classbeat_queue", "classbeat_queue"),
    ("GRANT classbeat_queue TO classbeat_owner", "classbeat_queue"),
    ("ALTER ROLE classbeat_queue LOGIN", "classbeat_queue"),
    ("ALTER ROLE classbeat_queue NOBYPASSRLS", "classbeat_queue"),
    ("ALTER ROLE classbeat_queue INHERIT", "classbeat_queue"),
    ("ALTER ROLE classbeat_queue SET work_mem = '64MB'", "classbeat_queue"),
]


@pytest.mark.parametrize(("statement", "subject"), DRIFT)
async def test_drift_is_found_refused_and_repaired(
    admin: Connection, database_url: str, app_database_url: str, statement: str, subject: str
) -> None:
    admin.execute(text(statement))
    try:
        found = check_queue(admin)
        assert any(subject in problem for problem in found), found
        startup, connection = await startup_and_connection_problems(app_database_url)
        assert "job queue does not match ADR 0005" in startup
        assert "job queue does not match ADR 0005" in connection
        assert subject in startup
    finally:
        install_queue(database_url)
    assert check_queue(admin) == []
    assert await startup_and_connection_problems(app_database_url) == ("", "")


async def test_the_check_reports_a_missing_role_and_a_malformed_row() -> None:
    missing = queue_problems((None, 0, 0, [], [], []))
    assert missing == [
        "the role classbeat_queue does not exist (run docker/postgres/queue/20-queue.sql)"
    ]
    with pytest.raises(ValueError, match="six columns"):
        queue_problems((None,))
    assert "classbeat_queue" in QUEUE_QUERY


async def test_a_database_down_at_startup_is_not_verified_until_a_connection_passes(
    admin: Connection, unreachable_database_url: str, app_database_url: str
) -> None:
    down = create_engine(app_settings(unreachable_database_url))
    try:
        await verify_database_role(down)  # no error: nothing to check yet
        assert not queue_verified(down)  # so the worker claims nothing
    finally:
        await down.dispose()
    engine = create_engine(app_settings(app_database_url))
    try:
        assert not queue_verified(engine)
        async with engine.connect() as connection:  # the per-connection check passes
            await connection.execute(text("SELECT 1"))
        assert queue_verified(engine)
    finally:
        await engine.dispose()
    unchecked = create_engine(app_settings(app_database_url), check_queue=False)
    try:
        await verify_database_role(unchecked)
        assert not queue_verified(unchecked)
    finally:
        await unchecked.dispose()


async def test_web_and_worker_refuse_to_start_on_a_drifted_queue(
    admin: Connection, database_url: str, app_database_url: str
) -> None:
    admin.execute(text("GRANT EXECUTE ON FUNCTION public.queue_stats() TO PUBLIC"))
    try:
        app = create_app(app_settings(app_database_url, app_secret_key=APP_SECRET))
        with pytest.raises(DatabaseRoleError, match="job queue"):
            async with app.router.lifespan_context(app):
                pytest.fail("the app must not start")
        with pytest.raises(DatabaseRoleError, match="job queue"):
            await _run(app_settings(app_database_url))
    finally:
        install_queue(database_url)
