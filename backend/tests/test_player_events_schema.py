"""Migration 0050: ``player_events`` matches ``classbeat.player.schema`` and keeps its rules
(ADR 0034 section 2): no foreign key and no user column, the CHECKs on every value, a unique
``jti``, and the app role's DML without TRUNCATE."""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from classbeat.db import SYSTEM_TABLES
from classbeat.player.schema import player_events

GOOD = {"kind": "account_deleted", "sub": "s" * 43, "jti": "j" * 43}


@pytest.fixture
def admin(auth_schema: None, database_url: str) -> Iterator[Connection]:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("TRUNCATE player_events"))
        connection.commit()
        yield connection
        connection.rollback()
    engine.dispose()


def test_the_columns_and_indexes_match_the_core_table(admin: Connection) -> None:
    live = {
        name: (nullable == "YES", default is not None)
        for name, nullable, default in admin.execute(
            text(
                "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'player_events'"
            )
        )
    }
    assert live == {
        c.name: (bool(c.nullable), c.server_default is not None) for c in player_events.columns
    }
    indexes = set(
        admin.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'player_events'")
        ).scalars()
    )
    assert {i.name for i in player_events.indexes} <= indexes
    assert "player_events" in SYSTEM_TABLES


def test_no_foreign_key_no_user_column_and_no_row_security(admin: Connection) -> None:
    assert (
        admin.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'public.player_events'::regclass AND contype = 'f'"
            )
        ).scalar_one()
        == 0
    )
    assert not {"user_id", "owner_id"} & {c.name for c in player_events.columns}
    assert not admin.execute(
        text("SELECT relrowsecurity FROM pg_class WHERE relname = 'player_events'")
    ).scalar_one()
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert admin.execute(
            text("SELECT has_table_privilege('classbeat_app', 'public.player_events', :p)"),
            {"p": privilege},
        ).scalar_one()
    assert not admin.execute(
        text("SELECT has_table_privilege('classbeat_app', 'public.player_events', 'TRUNCATE')")
    ).scalar_one()


def insert(admin: Connection, **values: Any) -> None:
    row = {**GOOD, **values}
    names = ", ".join(row)
    admin.execute(
        text(f"INSERT INTO player_events ({names}) VALUES ({', '.join(':' + k for k in row)})"),  # noqa: S608
        row,
    )


def test_a_good_row_gets_its_defaults(admin: Connection) -> None:
    insert(admin)
    row = admin.execute(
        text(
            "SELECT state, attempt, lease_version, give_up_at - created_at AS life, "
            "available_at = created_at AS due FROM player_events"
        )
    ).one()
    assert (row.state, row.attempt, row.lease_version, row.due) == ("pending", 0, 0, True)
    assert row.life.days == 7


@pytest.mark.parametrize(
    "values",
    [
        {"kind": "other"},
        {"sub": "s" * 42},
        {"sub": "s" * 44},
        {"sub": "s" * 42 + "="},
        {"jti": "j" * 42 + "+"},
        {"state": "delivered"},
        {"attempt": -1},
        {"attempt": 41},
        {"leased_by": "w1"},  # a lease without its expiry
        {"leased_by": "bad worker", "lease_expires_at": "2026-01-01T00:00:00Z"},
        {"state": "failed", "leased_by": "w1", "lease_expires_at": "2026-01-01T00:00:00Z"},
        {"last_error": "Some Text"},
        {"last_error": "x" * 33},
        {"give_up_at": "2000-01-01T00:00:00Z"},
    ],
)
def test_a_bad_value_is_refused(admin: Connection, values: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        insert(admin, **values)


def test_a_jti_is_used_once(admin: Connection) -> None:
    insert(admin)
    with pytest.raises(IntegrityError):
        insert(admin, sub="t" * 43)
