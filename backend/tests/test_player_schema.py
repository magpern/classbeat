"""Migration 0050 (ADR 0034 section 3, #1059 criterion 4): ``connections.provider`` gains
``player`` and the owned ``player_link_flows`` table. The guards, two users' isolation in both
tables, the checks, and the migration both ways with player rows present."""

import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from classbeat.db import RETENTION, SYSTEM_TABLES
from dbtools import alembic_config, migrated
from jobtools import as_user, engine_for, new_user
from retention_guard import check_retention
from rls_guard import check_tables
from role_guard import check_roles
from test_classes_schema import count, run_as

CALLBACK = "https://classbeat.test/connections/player/callback"
VERIFIER = "A" * 43
CONNECTION = (
    "INSERT INTO connections (owner_id, provider, status, secret, secret_nonce, wrapped_dek, "
    "dek_nonce, key_id) VALUES (:o, 'player', 'connected', '\\x00', :n, :w, :n, 'kek-1')"
)


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url) as engine:
        yield engine


def sha(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def flow(conn: Connection, owner: uuid.UUID, state: str = "s", **c: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "o": owner, "h": sha(state), "s": sha("x"), "v": VERIFIER, "r": CALLBACK,
        "life": "10 minutes", **c,
    }  # fmt: skip
    return conn.execute(
        text(
            "INSERT INTO player_link_flows (owner_id, state_hash, session_hash, verifier, "
            "redirect_uri, expires_at) VALUES (:o, :h, :s, :v, :r, "
            "now() + CAST(:life AS interval)) RETURNING id"
        ),
        values,
    ).scalar_one()


def connection(conn: Connection, owner: uuid.UUID) -> None:
    conn.execute(text(CONNECTION), {"o": owner, "n": bytes(12), "w": bytes(48)})


def test_the_new_table_meets_the_guards(admin: Connection) -> None:
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_roles(admin) == []
    assert check_retention(admin, RETENTION, SYSTEM_TABLES) == []
    policies = admin.execute(
        text("SELECT policyname FROM pg_policies WHERE tablename = 'player_link_flows'")
    ).scalars()
    assert list(policies) == ["owner_isolation"]
    forced = admin.execute(
        text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
             "WHERE relname = 'player_link_flows'")
    ).scalar_one()  # fmt: skip
    assert forced is True
    assert "player_link_flows" in RETENTION


def test_two_users_share_nothing(admin: Connection, app: Engine) -> None:
    a, b = new_user(admin), new_user(admin)
    flow(admin, a)
    connection(admin, a)
    with app.begin() as conn:
        as_user(conn, b)
        assert count(conn, "player_link_flows") == 0
        assert count(conn, "connections") == 0
        for table in ("player_link_flows", "connections"):
            assert conn.execute(text(f"UPDATE {table} SET owner_id = owner_id")).rowcount == 0  # noqa: S608
            assert conn.execute(text(f"DELETE FROM {table}")).rowcount == 0  # noqa: S608
        taken = conn.execute(
            text("UPDATE player_link_flows SET used_at = now() WHERE state_hash = :h "
                 "AND session_hash = :s RETURNING verifier"),
            {"h": sha("s"), "s": sha("x")},
        ).all()  # fmt: skip
        assert taken == []
    assert count(admin, "player_link_flows") == 1
    assert count(admin, "connections") == 1
    with pytest.raises(DBAPIError):  # B claiming A's owner_id: the policy's WITH CHECK
        run_as(app, b, CONNECTION, {"o": a, "n": bytes(12), "w": bytes(48)})
    with pytest.raises(DBAPIError):
        run_as(
            app,
            b,
            "INSERT INTO player_link_flows (owner_id, state_hash, session_hash, verifier, "
            "redirect_uri, expires_at) VALUES (:o, :h, :h, :v, :r, now())",
            {"o": a, "h": sha("b"), "v": VERIFIER, "r": CALLBACK},
        )


@pytest.mark.parametrize(
    "change",
    [
        {"life": "11 minutes"},
        {"v": "short"},
        {"v": "A" * 42 + "!"},
        {"h": b"\x00"},
        {"s": b"\x00" * 31},
        {"r": ""},
        {"r": "x" * 2049},
    ],
)
def test_flow_checks(admin: Connection, change: dict[str, Any]) -> None:
    owner = new_user(admin)
    with pytest.raises(IntegrityError):
        flow(admin, owner, **change)


def test_a_state_is_unique_per_owner_and_one_player_row_per_user(admin: Connection) -> None:
    a, b = new_user(admin), new_user(admin)
    flow(admin, a)
    flow(admin, b)
    with pytest.raises(IntegrityError):
        flow(admin, a)
    connection(admin, a)
    with pytest.raises(IntegrityError):
        connection(admin, a)


def test_flows_go_with_the_account(admin: Connection) -> None:
    owner = new_user(admin)
    flow(admin, owner)
    admin.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    assert count(admin, "player_link_flows") == 0


def test_upgrade_and_downgrade_with_player_rows(owner_database_url: str, database_url: str) -> None:
    config = alembic_config()
    admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        with migrated(owner_database_url, "0049_oidc_provider"):
            command.upgrade(config, "0050_player_connection")  # empty
            command.downgrade(config, "0049_oidc_provider")
            command.upgrade(config, "0050_player_connection")
            with admin_engine.connect() as conn:
                owner = new_user(conn)
                connection(conn, owner)
                flow(conn, owner)
                conn.execute(text(CONNECTION.replace("'player'", "'mowl'")),
                             {"o": owner, "n": bytes(12), "w": bytes(48)})  # fmt: skip
            command.downgrade(config, "0049_oidc_provider")
            with admin_engine.connect() as conn:
                providers = conn.execute(text("SELECT provider FROM connections")).scalars()
                assert list(providers) == ["mowl"]
                tables = conn.execute(
                    text("SELECT count(*) FROM pg_tables WHERE tablename = 'player_link_flows'")
                ).scalar_one()
                assert tables == 0
                with pytest.raises(IntegrityError):
                    connection(conn, owner)
            command.upgrade(config, "0050_player_connection")
    finally:
        admin_engine.dispose()
