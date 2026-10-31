"""Guard 12 (ADR 0014; #337): every reference to a user and every owned table is decided, read
from the migrated schema, and the guard can fail."""

from collections.abc import Iterator
from types import MappingProxyType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.account.coverage import RETAINED, UNLINKED_IDS, USER_REFERENCES
from classbeat.account.registry import NOT_EXPORTED, SECTIONS, Section
from classbeat.db import RETENTION, SYSTEM_TABLES
from gdpr_guard import check_export_registry, check_user_references
from jobtools import engine_for


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def test_every_reference_to_a_user_is_decided(admin: Connection) -> None:
    assert check_user_references(admin, USER_REFERENCES, UNLINKED_IDS) == []


def test_an_undecided_foreign_key_to_users_fails(admin: Connection) -> None:
    admin.execute(
        text(
            "CREATE TABLE gdpr_probe (id uuid PRIMARY KEY, owner_id uuid NOT NULL "
            "REFERENCES users (id) ON DELETE CASCADE, reviewer uuid REFERENCES users (id))"
        )
    )
    try:
        assert check_user_references(admin, USER_REFERENCES, UNLINKED_IDS) == [
            "gdpr_probe.owner_id: foreign key to users with no decision (ADR 0014)",
            "gdpr_probe.reviewer: foreign key to users with no decision (ADR 0014)",
        ]
    finally:
        admin.execute(text("DROP TABLE gdpr_probe"))


def test_a_user_id_column_without_a_foreign_key_fails(admin: Connection) -> None:
    admin.execute(text("CREATE TABLE gdpr_probe (id uuid PRIMARY KEY, approved_by uuid)"))
    try:
        assert check_user_references(admin, USER_REFERENCES, UNLINKED_IDS) == [
            "gdpr_probe.approved_by: a user-looking uuid, no foreign key, no decision"
        ]
    finally:
        admin.execute(text("DROP TABLE gdpr_probe"))


def test_a_wrong_action_and_a_stale_entry_fail(admin: Connection) -> None:
    wrong = MappingProxyType(
        {**USER_REFERENCES, ("sessions", "user_id"): "n", ("gone", "owner_id"): "c"}
    )
    assert check_user_references(admin, wrong, UNLINKED_IDS) == [
        "gone.owner_id: listed but not a foreign key to users",
        "sessions.user_id: database says c, list says n",
    ]
    assert check_user_references(admin, USER_REFERENCES, UNLINKED_IDS | {("jobs", "kind")}) == [
        "jobs.kind: listed as an unlinked user id but not a uuid column"
    ]


def test_the_cascades_are_what_the_list_says_and_events_cascade(admin: Connection) -> None:
    rows = dict(
        admin.execute(
            text(
                "SELECT conrelid::regclass::text || '.' || a.attname, confdeltype::text "
                "FROM pg_constraint c JOIN pg_attribute a "
                "ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
                "WHERE contype = 'f' AND confrelid = 'public.users'::regclass"
            )
        ).all()
    )
    assert rows["auth_events.user_id"] == "c"  # migration 0015: the address goes with the user
    assert RETAINED.keys() == {("admin_audit", "actor_email")}


def test_every_owned_table_is_exported_or_has_a_reason(admin: Connection) -> None:
    assert check_export_registry(admin, SECTIONS, NOT_EXPORTED, SYSTEM_TABLES) == []
    assert set(RETENTION) == {s.table for s in SECTIONS} | set(NOT_EXPORTED)


def test_the_export_guard_can_fail(admin: Connection) -> None:
    admin.execute(
        text(
            "CREATE TABLE gdpr_probe (id uuid PRIMARY KEY, owner_id uuid NOT NULL "
            "REFERENCES users (id) ON DELETE CASCADE, token_hash bytea)"
        )
    )
    try:
        assert check_export_registry(admin, SECTIONS, NOT_EXPORTED, SYSTEM_TABLES) == [
            "gdpr_probe: owned table neither exported nor given a reason (ADR 0014)"
        ]
        probe = Section("gdpr_probe", "gdpr_probe", "id", frozenset())
        assert check_export_registry(admin, (*SECTIONS, probe), NOT_EXPORTED, SYSTEM_TABLES) == [
            "gdpr_probe.token_hash: bytea column would be exported"
        ]
    finally:
        admin.execute(text("DROP TABLE gdpr_probe"))
    stale = Section("classes", "classes", "created_at, nope", frozenset({"missing"}))
    others = tuple(s for s in SECTIONS if s.table != "classes")
    assert check_export_registry(admin, (*others, stale), NOT_EXPORTED, SYSTEM_TABLES) == [
        "classes.missing: omitted but not a column",
        "classes.nope: ordered by a column that does not exist",
    ]
    assert check_export_registry(admin, others, NOT_EXPORTED, SYSTEM_TABLES) == [
        "classes: owned table neither exported nor given a reason (ADR 0014)"
    ]
