"""Migration 0053 (ADR 0036 section 4.1, SR C4): ``swedebeat_track_maps`` and
``swedebeat_track_downloads``. The contract and guards (RLS forced, retention, maint), two users'
isolation, the table's checks, and the deletion paths: the account (cascade), the SwedeBeat
Disconnect and a keys wipe (``connections.forget``), not a MOWL Disconnect; the GDPR export holds a
count only. The job that writes the rows is ``test_swedebeat_track_analysis.py``. Never the network.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from authtools import Env
from classbeat import connections
from classbeat.account.export import build_export
from classbeat.account.registry import NOT_EXPORTED
from classbeat.connections.repository import delete_all_in
from classbeat.db import RETENTION, SYSTEM_TABLES, user_session
from ctx import make_context
from gatewayfake import FakeGateway, store_bearer
from jobtools import as_user, engine_for, new_user
from maint_guard import check_maint
from retention_guard import check_retention
from rls_guard import check_tables
from test_classes_schema import count, run_as

TABLES = ("swedebeat_track_maps", "swedebeat_track_downloads")
MAP = (
    "INSERT INTO swedebeat_track_maps (owner_id, reference, analyser_version, sha256, map) "
    "VALUES (:o, :r, :v, :h, CAST(:m AS jsonb))"
)
REFERENCE_MARK = "PlantedTrackRef-Q7"


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


def plant(conn: Connection, owner: uuid.UUID, reference: str = "trk-1", **kw: Any) -> None:
    params = {"o": owner, "r": reference, "v": 1, "h": bytes(32), "m": '{"track_version": 1}'}
    conn.execute(text(MAP), params | kw)
    conn.execute(text("INSERT INTO swedebeat_track_downloads (owner_id) VALUES (:o)"), {"o": owner})


def maps(conn: Connection, owner: uuid.UUID) -> int:
    statement = text("SELECT count(*) FROM swedebeat_track_maps WHERE owner_id = :o")
    return conn.execute(statement, {"o": owner}).scalar_one()


def downloads(conn: Connection, owner: uuid.UUID) -> int:
    statement = text("SELECT count(*) FROM swedebeat_track_downloads WHERE owner_id = :o")
    return conn.execute(statement, {"o": owner}).scalar_one()


def test_the_new_tables_meet_the_guards(admin: Connection) -> None:
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_retention(admin, RETENTION, SYSTEM_TABLES) == []
    assert check_maint(admin) == []
    for table in TABLES:
        policies = admin.execute(
            text("SELECT policyname, permissive FROM pg_policies WHERE tablename = :t"),
            {"t": table},
        ).all()
        assert [tuple(p) for p in policies] == [("owner_isolation", "PERMISSIVE")], table
        forced = admin.execute(
            text("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).scalar_one()
        assert forced is True, table
        assert table in NOT_EXPORTED  # a count (maps) or nothing (downloads), never the rows
    assert RETENTION["swedebeat_track_maps"].deleted_with == {"account", "swedebeat_connection"}
    assert RETENTION["swedebeat_track_maps"].columns == {"reference", "sha256", "map"}
    assert RETENTION["swedebeat_track_downloads"].deleted_with == {"account"}


def test_another_owners_rows_are_invisible_and_cannot_be_written(
    admin: Connection, app: Engine
) -> None:
    a, b = new_user(admin), new_user(admin)
    plant(admin, a, REFERENCE_MARK)
    with app.begin() as connection:
        as_user(connection, b)
        for table in TABLES:
            assert count(connection, table) == 0, table
            assert connection.execute(text(f"UPDATE {table} SET owner_id = owner_id")).rowcount == 0  # noqa: S608
            assert connection.execute(text(f"DELETE FROM {table}")).rowcount == 0  # noqa: S608
        named = text("SELECT count(*) FROM swedebeat_track_maps WHERE reference = :r")
        assert connection.execute(named, {"r": REFERENCE_MARK}).scalar_one() == 0
    with app.begin() as connection:
        as_user(connection, a)
        assert count(connection, "swedebeat_track_maps") == 1
    assert (maps(admin, a), downloads(admin, a)) == (1, 1)
    # B writing rows that claim A's owner: refused by the policy's WITH CHECK.
    claim = {"o": a, "r": "trk-b", "v": 1, "h": bytes(32), "m": "{}"}
    with pytest.raises(DBAPIError):
        run_as(app, b, MAP, claim)
    with pytest.raises(DBAPIError):
        run_as(app, b, "INSERT INTO swedebeat_track_downloads (owner_id) VALUES (:o)", {"o": a})
    # B's own row of the same reference is B's: the key includes the owner.
    run_as(app, b, MAP, {**claim, "o": b, "r": REFERENCE_MARK})
    assert (maps(admin, a), maps(admin, b)) == (1, 1)


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ({"r": "bad/ref"}, "swedebeat_track_maps_reference"),
        ({"r": ""}, "swedebeat_track_maps_reference"),
        ({"r": "x" * 65}, "swedebeat_track_maps_reference"),
        ({"v": 0}, "swedebeat_track_maps_analyser_version"),
        ({"h": bytes(31)}, "swedebeat_track_maps_sha256"),
        ({"m": '{"x": "' + "y" * (1 << 20) + '"}'}, "swedebeat_track_maps_map"),
    ],
)
def test_the_checks_bound_each_row(
    admin: Connection, change: dict[str, Any], constraint: str
) -> None:
    owner = new_user(admin)
    params = {"o": owner, "r": "trk-1", "v": 1, "h": bytes(32), "m": "{}"} | change
    with pytest.raises(IntegrityError) as raised:
        admin.execute(text(MAP), params)
    assert constraint in str(raised.value)


def test_one_row_per_owner_reference_and_analyser_version(admin: Connection) -> None:
    owner = new_user(admin)
    plant(admin, owner, "trk-1")
    plant(admin, owner, "trk-1", v=2)  # another analyser version is another row
    with pytest.raises(IntegrityError) as raised:
        admin.execute(text(MAP), {"o": owner, "r": "trk-1", "v": 1, "h": bytes(32), "m": "{}"})
    assert "swedebeat_track_maps_owner_id_reference_analyser_version_key" in str(raised.value)


def test_the_rows_go_with_the_account(admin: Connection, app: Engine) -> None:
    a, b = new_user(admin), new_user(admin)
    plant(admin, a)
    plant(admin, b)
    with app.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": a})
    assert (maps(admin, a), downloads(admin, a)) == (0, 0)
    assert (maps(admin, b), downloads(admin, b)) == (1, 1)


@pytest.fixture
async def account_env(clean_auth: None, app_database_url: str) -> Any:
    from authtools import running_env

    async with running_env(app_database_url) as running:
        yield running


async def _swedebeat_user(env: Env, admin: Connection) -> uuid.UUID:
    owner = await env.user(f"track-{uuid.uuid4().hex[:10]}@example.com")
    ring = env.app.state.keyring
    await store_bearer(env.factory, ring, make_context(owner), FakeGateway({"k"}))
    await connections.save(env.factory, ring, make_context(owner), "mowl", {"token": "t"})
    plant(admin, owner)
    return owner


async def test_a_swedebeat_disconnect_forgets_only_that_users_track_maps(
    account_env: Env, admin: Connection
) -> None:
    owner = await _swedebeat_user(account_env, admin)
    other = await _swedebeat_user(account_env, admin)
    # A MOWL Disconnect keeps them: they belong to the SwedeBeat connection.
    assert await connections.disconnect(account_env.factory, make_context(owner), "mowl")
    assert maps(admin, owner) == 1
    assert await connections.disconnect(account_env.factory, make_context(owner), "swedebeat")
    assert maps(admin, owner) == 0
    assert downloads(admin, owner) == 1  # the daily limit's record stays: no fresh day
    assert (maps(admin, other), downloads(admin, other)) == (1, 1)


async def test_a_keys_wipe_forgets_the_track_maps(account_env: Env, admin: Connection) -> None:
    owner = await _swedebeat_user(account_env, admin)
    other = await _swedebeat_user(account_env, admin)
    async with user_session(account_env.factory, make_context(owner)) as db:
        assert await delete_all_in(db, make_context(owner)) == ["mowl", "swedebeat"]
    assert (maps(admin, owner), maps(admin, other)) == (0, 1)


async def test_the_export_holds_a_count_only(account_env: Env, admin: Connection) -> None:
    owner = await _swedebeat_user(account_env, admin)
    plant(admin, owner, REFERENCE_MARK)
    other = await _swedebeat_user(account_env, admin)
    plant(admin, other, "trk-other")
    document = await build_export(account_env.factory, make_context(owner))
    assert document is not None
    assert document["swedebeat_track_analyses"] == 2
    assert REFERENCE_MARK not in str(document)
    assert "swedebeat_track_maps" not in document
    assert "swedebeat_track_downloads" not in document
