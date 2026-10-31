"""The audited maint transaction (ADR 0011 section 5, #210): ``admin_session`` reads read only
after its audit row, writes only with ``write=True``, leaves exactly one row per call, and the
maint pool hands out no connection carrying an earlier session's state; ``AdminDb.verify``
refuses a wrong role."""

from collections.abc import AsyncIterator

import psycopg
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError, DisconnectionError

from admintools import audit_rows, new_admin
from classbeat.admin import audit
from classbeat.admin.audit import Params, Target, UnknownAction
from classbeat.admin.db import (
    MAINT_ROLE,
    Actor,
    AdminDb,
    AdminRefused,
    MaintRoleError,
    admin_session,
    audit_refusal,
    check_checkout,
)
from classbeat.config import Settings
from classbeat.db import TransactionOwnedError

ACTION = "test.read"


@pytest.fixture(autouse=True)
def _test_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(audit.ACTIONS, ACTION, Params)


@pytest.fixture
async def db(maint_database_url: str) -> AsyncIterator[AdminDb]:
    admin_db = AdminDb(make_url(maint_database_url))
    try:
        yield admin_db
    finally:
        await admin_db.dispose()


@pytest.fixture
def actor(superuser: Connection) -> Actor:
    return new_admin(superuser)


@pytest.fixture
def target(superuser: Connection) -> Target:
    from jobtools import new_user

    return Target(user_id=new_user(superuser), kind="user")


async def test_a_read_is_read_only_after_its_audit_row(
    db: AdminDb, actor: Actor, superuser: Connection, target: Target
) -> None:
    async with admin_session(db, actor, ACTION, target=target) as session:
        assert (await session.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
        for statement in (
            "INSERT INTO app_settings (key, value) VALUES ('probe', '{}'::jsonb)",
            "UPDATE users SET disabled_at = now() WHERE email LIKE '%@example.test'",
            "SET TRANSACTION READ WRITE",
            "SET transaction_read_only = off",
            "SET LOCAL transaction_read_only = off",
        ):
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(text(statement))
        assert (await session.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
        email = (
            await session.execute(
                text("SELECT email FROM users WHERE id = :u"), {"u": target.user_id}
            )
        ).scalar_one()
        assert email.endswith("@example.test")
    assert audit_rows(superuser, actor) == [("ok", None)]
    disabled = superuser.execute(
        text("SELECT disabled_at FROM users WHERE id = :u"), {"u": target.user_id}
    ).scalar_one()
    assert disabled is None


async def test_a_write_session_writes_with_its_audit_row(
    db: AdminDb, actor: Actor, superuser: Connection, target: Target
) -> None:
    async with admin_session(db, actor, ACTION, write=True, target=target) as session:
        assert (await session.execute(text("SHOW transaction_read_only"))).scalar_one() == "off"
        await session.execute(
            text("UPDATE users SET disabled_at = now() WHERE id = :u"), {"u": target.user_id}
        )
    assert audit_rows(superuser, actor) == [("ok", None)]
    row = superuser.execute(
        text("SELECT target_user_id, target_kind FROM admin_audit WHERE request_id = :r"),
        {"r": actor.request_id},
    ).one()
    assert tuple(row) == (target.user_id, "user")
    assert superuser.execute(
        text("SELECT disabled_at IS NOT NULL FROM users WHERE id = :u"), {"u": target.user_id}
    ).scalar_one()


async def test_a_failed_write_rolls_back_with_its_audit_row_and_leaves_one_error_row(
    db: AdminDb, actor: Actor, superuser: Connection, target: Target
) -> None:
    async def fail() -> None:
        async with admin_session(db, actor, ACTION, write=True, target=target) as session:
            await session.execute(
                text("UPDATE users SET disabled_at = now() WHERE id = :u"), {"u": target.user_id}
            )
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await fail()
    assert audit_rows(superuser, actor) == [("error", "internal")]
    assert (
        superuser.execute(
            text("SELECT disabled_at FROM users WHERE id = :u"), {"u": target.user_id}
        ).scalar_one()
        is None
    )


async def test_a_read_failing_after_the_audit_insert_leaves_one_error_row(
    db: AdminDb, actor: Actor, superuser: Connection
) -> None:
    with pytest.raises(DBAPIError):
        async with admin_session(db, actor, ACTION) as session:
            await session.execute(text("SELECT password_hash FROM users"))  # not granted
    assert audit_rows(superuser, actor) == [("error", "internal")]


async def test_a_refusal_leaves_one_refused_row(
    db: AdminDb, actor: Actor, superuser: Connection
) -> None:
    with pytest.raises(AdminRefused):
        async with admin_session(db, actor, ACTION):
            raise AdminRefused("target_is_admin")
    assert audit_rows(superuser, actor) == [("refused", "target_is_admin")]


async def test_the_caller_cannot_end_the_transaction(
    db: AdminDb, actor: Actor, superuser: Connection
) -> None:
    with pytest.raises(TransactionOwnedError):
        async with admin_session(db, actor, ACTION) as session:
            await session.commit()
    assert audit_rows(superuser, actor) == [("error", "internal")]


async def test_an_unknown_action_or_bad_params_writes_nothing(
    db: AdminDb, actor: Actor, superuser: Connection
) -> None:
    with pytest.raises(UnknownAction):
        async with admin_session(db, actor, "no.such_action"):
            pass
    with pytest.raises(UnknownAction):
        async with admin_session(db, actor, ACTION, params={"token": "x"}):
            pass
    with pytest.raises(UnknownAction):
        await audit_refusal(db, actor, "admin.request", "rate_limited", params={"route": "/x"})
    assert audit_rows(superuser, actor) == []


async def test_refusal_rows_are_limited_per_admin(
    db: AdminDb, actor: Actor, superuser: Connection
) -> None:
    params = {"method": "GET", "route": "/api/admin/users"}
    written = [
        await audit_refusal(db, actor, "admin.request", "mfa_required", params=params)
        for _ in range(25)
    ]
    assert written.count(True) == 20
    assert audit_rows(superuser, actor) == [("refused", "mfa_required")] * 20


@pytest.mark.parametrize(
    "statement",
    [
        "SET default_transaction_read_only = off",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
        "SELECT set_config('app.user_id', '00000000-0000-0000-0000-000000000001', false)",
        "SET statement_timeout = 0",
    ],
)
async def test_session_state_does_not_reach_the_next_checkout(
    db: AdminDb, actor: Actor, statement: str
) -> None:
    async with admin_session(db, actor, ACTION, write=True) as session:
        pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        await session.execute(text(statement))
    async with admin_session(db, actor, ACTION) as session:
        state = (
            await session.execute(
                text(
                    "SELECT pg_backend_pid(), current_setting('default_transaction_read_only'), "
                    "current_setting('transaction_read_only'), "
                    "coalesce(current_setting('app.user_id', true), ''), "
                    "current_setting('statement_timeout')"
                )
            )
        ).one()
    # Reset by DISCARD ALL on the same connection, or (had it failed) a fresh connection.
    assert tuple(state[1:]) == ("on", "on", "", "30s")
    assert state[0] == pid  # the reset, not a reconnect, did it


@pytest.mark.parametrize(
    "statement",
    [
        "SET default_transaction_read_only = off",
        "SET lock_timeout = 0",
    ],
)
def test_check_checkout_refuses_a_drifted_connection(
    maint_database_url: str, statement: str
) -> None:
    url = make_url(maint_database_url)
    conninfo = (
        f"host={url.host} port={url.port} dbname={url.database} user={url.username} "
        f"password={url.password}"
    )
    with psycopg.connect(conninfo) as raw:
        check_checkout(raw)  # as the role leaves it
        with raw.cursor() as cursor:
            cursor.execute(statement.encode())
        raw.commit()
        with pytest.raises(DisconnectionError):
            check_checkout(raw)


async def test_verify_accepts_the_installed_role(db: AdminDb) -> None:
    await db.verify()


async def _verify(url: str) -> None:
    other = AdminDb(make_url(url))
    try:
        await other.verify()
    finally:
        await other.dispose()


async def test_verify_refuses_another_role(database_url: str, app_database_url: str) -> None:
    with pytest.raises(MaintRoleError, match="superuser"):
        await _verify(database_url)
    with pytest.raises(MaintRoleError, match="another role"):
        await _verify(app_database_url)


async def test_verify_refuses_wrong_credentials(maint_database_url: str) -> None:
    wrong = make_url(maint_database_url).set(password="wrong").render_as_string(False)
    with pytest.raises(MaintRoleError, match="credentials") as caught:
        await _verify(wrong)
    assert "wrong" not in str(caught.value)


@pytest.mark.parametrize(
    ("change", "undo", "message"),
    [
        (f"GRANT classbeat_app TO {MAINT_ROLE}", f"REVOKE classbeat_app FROM {MAINT_ROLE}",
         "member of a role"),
        (f"ALTER ROLE {MAINT_ROLE} SET work_mem = '8MB'", f"ALTER ROLE {MAINT_ROLE} RESET work_mem",
         "role settings"),
        (f"ALTER ROLE {MAINT_ROLE} RESET default_transaction_read_only",
         f"ALTER ROLE {MAINT_ROLE} SET default_transaction_read_only = on", "role settings"),
        (f"ALTER ROLE {MAINT_ROLE} INHERIT", f"ALTER ROLE {MAINT_ROLE} NOINHERIT", "inherits"),
        (f"ALTER ROLE {MAINT_ROLE} NOBYPASSRLS", f"ALTER ROLE {MAINT_ROLE} BYPASSRLS",
         "BYPASSRLS"),
    ],
)  # fmt: skip
async def test_verify_refuses_a_drifted_role(
    maint_database_url: str, superuser: Connection, change: str, undo: str, message: str
) -> None:
    superuser.execute(text(change))
    try:
        with pytest.raises(MaintRoleError, match=message):
            await _verify(maint_database_url)
    finally:
        superuser.execute(text(undo))
    await _verify(maint_database_url)


def test_from_settings_needs_the_url_and_the_password() -> None:
    url = SecretStr("postgresql+psycopg://classbeat_maint@db:5432/classbeat")
    pw, empty = SecretStr("pw"), SecretStr("")
    assert Settings(maint_database_url=url).maint_sqlalchemy_url() is None
    assert Settings(database_maint_password=pw).maint_sqlalchemy_url() is None
    assert (
        Settings(maint_database_url=url, database_maint_password=empty).maint_sqlalchemy_url()
        is None
    )
    built = Settings(maint_database_url=url, database_maint_password=pw).maint_sqlalchemy_url()
    assert built is not None
    assert built.username == MAINT_ROLE
    assert built.password == "pw"
