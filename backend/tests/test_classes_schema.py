"""Migration 0005 (ADR 0007 sections 1, 2, 10 and 11): classes, conversation, saved workouts and
durable usage. Contract, isolation, immutability, cascades and the guards."""

import json
import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from classbeat.db import RETENTION, SYSTEM_TABLES
from dbtools import alembic_config, migrated
from jobtools import as_user, digest, engine_for, new_class, new_job, new_user
from queue_guard import check_queue
from retention_guard import check_retention
from rls_guard import check_tables
from role_guard import check_roles

NEW_TABLES = ("class_messages", "saved_workouts")
ATTACH, SAVE, UPDATE = "mowl_attach_spotify_playlist", "mowl_save_workout", "mowl_update_workout"
RUN = {
    "stage": "design",
    "attempt": 1,
    "provider": "fake",
    "model": "m",
    "effort": "medium",
    "prompt_version": "v",
}
NEW_CATEGORIES = ("program_changed", "program_missing", "music_unlinked")


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


def insert(conn: Connection, table: str, **values: Any) -> uuid.UUID:
    """A row written as the superuser (setup only); jsonb columns take Python values."""
    values = {"id": uuid.uuid4(), **values}
    jsonb = {"content", "music", "provenance", "change", "request_context", "result"}
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k in jsonb else f":{k}" for k in values)
    row = {k: json.dumps(v) if k in jsonb else v for k, v in values.items()}
    return conn.execute(
        text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({params}) RETURNING id"),  # noqa: S608
        row,
    ).scalar_one()


def revision(conn: Connection, owner: uuid.UUID, class_id: uuid.UUID, number: int = 1, **c: Any):
    return insert(
        conn, "class_revisions", owner_id=owner, class_id=class_id, number=number, kind="draft",
        content={}, content_hash=digest(f"c{number}"), music={}, provenance={}, **c,
    )  # fmt: skip


def message(
    conn: Connection, owner: uuid.UUID, class_id: uuid.UUID, seq: int = 1, **c: Any
) -> uuid.UUID:
    return insert(
        conn, "class_messages", owner_id=owner, class_id=class_id, seq=seq, role="user",
        body="Make the climb longer", **c,
    )  # fmt: skip


def operation(conn: Connection, owner: uuid.UUID, kind: str = SAVE, **c: Any) -> uuid.UUID:
    return insert(
        conn, "external_operations", owner_id=owner, kind=kind, idempotency_key=f"k{uuid.uuid4()}",
        request_hash=digest("op"), state="pending", lease_version=1, **c,
    )  # fmt: skip


def saved(conn: Connection, owner: uuid.UUID, class_id: uuid.UUID, program_id: int = 41, **c: Any):
    return insert(
        conn, "saved_workouts", owner_id=owner, class_id=class_id, program_id=program_id,
        revision_number=1, content_hash=digest("c1"), name="Tuesday climb", **c,
    )  # fmt: skip


def agent_run(conn: Connection, owner: uuid.UUID, job_id: uuid.UUID | None, **c: Any):
    return insert(
        conn, "agent_runs", owner_id=owner, job_id=job_id, stage="design", attempt=1,
        provider="fake", model="m", effort="medium", prompt_version="v", **c,
    )  # fmt: skip


def run_as(engine: Engine, user: uuid.UUID, statement: str, params: dict[str, Any]) -> None:
    """One statement as the app role under ``user``'s row security, in its own transaction."""
    with engine.begin() as connection:
        as_user(connection, user)
        connection.execute(text(statement), params)


def count(conn: Connection, table: str) -> int:
    return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608


def test_the_new_tables_meet_guards_1_2_9_and_10(admin: Connection) -> None:
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_roles(admin) == []
    assert check_queue(admin) == []
    assert check_retention(admin, RETENTION, SYSTEM_TABLES) == []
    policies = admin.execute(
        text(
            "SELECT tablename, policyname, permissive, cmd, roles::text[], qual, with_check "
            "FROM pg_policies WHERE tablename = ANY(:t) AND policyname <> 'owner_isolation'"
        ),
        {"t": [*NEW_TABLES, "agent_runs"]},
    ).all()
    r, public = "RESTRICTIVE", ["public"]
    assert sorted(tuple(p) for p in policies) == [
        ("agent_runs", "agent_runs_finished_immutable", r, "UPDATE", public, "(outcome IS NULL)",
         "true"),
        ("agent_runs", "agent_runs_no_delete", r, "DELETE", public, "false", None),
        ("class_messages", "messages_no_delete", r, "DELETE", public, "false", None),
        ("class_messages", "messages_no_update", r, "UPDATE", public, "false", None),
    ]  # fmt: skip
    for table in NEW_TABLES:
        permissive = (
            admin.execute(
                text(
                    "SELECT policyname FROM pg_policies WHERE tablename = :t "
                    "AND permissive = 'PERMISSIVE'"
                ),
                {"t": table},
            )
            .scalars()
            .all()
        )
        assert permissive == ["owner_isolation"], table


@pytest.mark.parametrize(
    ("table", "policy", "cmd"),
    [
        ("class_messages", "messages_no_update", "UPDATE"),
        ("class_messages", "messages_no_delete", "DELETE"),
        ("agent_runs", "agent_runs_no_delete", "DELETE"),
    ],
)
def test_guard_1_requires_the_deny_policies(
    admin: Connection, table: str, policy: str, cmd: str
) -> None:
    admin.execute(text(f"DROP POLICY {policy} ON public.{table}"))
    try:
        found = check_tables(admin, SYSTEM_TABLES)
        assert found == [f"{table}: needs a restrictive FOR {cmd} TO PUBLIC USING (false) policy"]
    finally:
        admin.execute(
            text(
                f"CREATE POLICY {policy} ON public.{table} AS RESTRICTIVE FOR {cmd} TO PUBLIC "
                "USING (false)"
            )
        )
    assert check_tables(admin, SYSTEM_TABLES) == []


def test_guard_1_requires_the_finished_usage_policy_with_its_check(admin: Connection) -> None:
    name = "agent_runs_finished_immutable"
    admin.execute(text(f"DROP POLICY {name} ON public.agent_runs"))
    try:
        for tail in ("USING (outcome IS NULL)", "USING (false) WITH CHECK (true)"):
            admin.execute(
                text(f"CREATE POLICY {name} ON public.agent_runs AS RESTRICTIVE FOR UPDATE {tail}")
            )
            assert check_tables(admin, SYSTEM_TABLES) == [
                "agent_runs: needs a restrictive FOR UPDATE TO PUBLIC USING ((outcome IS NULL)) "
                "WITH CHECK (true) policy"
            ]
            admin.execute(text(f"DROP POLICY {name} ON public.agent_runs"))
    finally:
        admin.execute(
            text(
                f"CREATE POLICY {name} ON public.agent_runs AS RESTRICTIVE FOR UPDATE "
                "USING (outcome IS NULL) WITH CHECK (true)"
            )
        )
    assert check_tables(admin, SYSTEM_TABLES) == []


def test_two_users_share_nothing_in_the_new_tables(admin: Connection, app: Engine) -> None:
    a, b = new_user(admin), new_user(admin)
    class_a = new_class(admin, a)
    class_b = new_class(admin, b)
    rev_a = revision(admin, a, class_a)
    message_a = message(admin, a, class_a, base_revision_id=rev_a)
    saved_a = saved(admin, a, class_a, revision_id=rev_a)
    with app.begin() as connection:
        as_user(connection, b)
        for table in NEW_TABLES:
            assert count(connection, table) == 0, table
        assert connection.execute(text("UPDATE saved_workouts SET name = 'mine'")).rowcount == 0
        assert connection.execute(text("DELETE FROM saved_workouts")).rowcount == 0
        assert (
            connection.execute(text("UPDATE classes SET title = 'mine'")).rowcount == 1
        )  # B's own
    claim = (  # B inserting a row that claims A's owner_id
        "INSERT INTO class_messages (owner_id, class_id, seq, role, body) "
        "VALUES (:o, :c, 2, 'user', 'x')"
    )
    with pytest.raises(DBAPIError):
        run_as(app, b, claim, {"o": a, "c": class_a})
    for statement, params in (
        # B's own rows pointing at A's parents: the owner-inclusive keys refuse them.
        (
            "INSERT INTO class_messages (owner_id, class_id, seq, role, body) "
            "VALUES (:b, :class_a, 1, 'user', 'x')",
            {"class_a": class_a},
        ),
        (
            "INSERT INTO class_messages (owner_id, class_id, seq, role, body, base_revision_id) "
            "VALUES (:b, :class_b, 1, 'user', 'x', :rev_a)",
            {"class_b": class_b, "rev_a": rev_a},
        ),
        (
            "INSERT INTO class_messages (owner_id, class_id, seq, role, body, job_id) "
            "VALUES (:b, :class_b, 1, 'user', 'x', :job_a)",
            {"class_b": class_b, "job_a": new_job(admin, a)},
        ),
        (
            "INSERT INTO saved_workouts (owner_id, class_id, program_id, revision_number, "
            "content_hash, name) VALUES (:b, :class_a, 5, 1, :h, 'n')",
            {"class_a": class_a, "h": digest("c1")},
        ),
        (
            "INSERT INTO saved_workouts (owner_id, class_id, program_id, revision_number, "
            "content_hash, name, operation_id) VALUES (:b, :class_b, 5, 1, :h, 'n', :op_a)",
            {"class_b": class_b, "h": digest("c1"), "op_a": operation(admin, a, class_id=class_a)},
        ),
        (
            "INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, "
            "state, lease_version, class_id) VALUES (:b, 'mowl_save_workout', 'k', :h, "
            "'pending', 1, :class_a)",
            {"class_a": class_a, "h": digest("op")},
        ),
        ("UPDATE classes SET current_revision_id = :rev_a WHERE id = :class_b",
         {"class_b": class_b, "rev_a": rev_a}),
    ):  # fmt: skip
        with pytest.raises(IntegrityError):
            run_as(app, b, statement, {"b": b, **params})
    assert (count(admin, "class_messages"), count(admin, "saved_workouts")) == (1, 1)
    assert (message_a, saved_a) != (None, None)


def test_a_revision_pointer_names_a_revision_of_the_same_class(admin: Connection) -> None:
    owner = new_user(admin)
    class_1, class_2 = new_class(admin, owner), new_class(admin, owner)
    rev_1, rev_2 = revision(admin, owner, class_1), revision(admin, owner, class_2)
    update = text("UPDATE classes SET current_revision_id = :r WHERE id = :c")
    admin.execute(update, {"r": rev_1, "c": class_1})
    with pytest.raises(IntegrityError):
        admin.execute(update, {"r": rev_2, "c": class_1})
    for column in ("base_revision_id", "result_revision_id"):
        with pytest.raises(IntegrityError):
            message(admin, owner, class_1, 1, **{column: rev_2})
    with pytest.raises(IntegrityError):
        saved(admin, owner, class_1, revision_id=rev_2)


def test_class_messages_cannot_be_changed_by_the_app_but_go_with_the_class(
    admin: Connection, app: Engine, owner_database_url: str
) -> None:
    owner = new_user(admin)
    class_1, class_2 = new_class(admin, owner), new_class(admin, owner)
    job_id = new_job(admin, owner, class_1)
    rev = revision(admin, owner, class_1)
    message(admin, owner, class_1, 1, job_id=job_id, base_revision_id=rev, result_revision_id=rev)
    message(admin, owner, class_2, 1)
    with app.connect() as connection:  # the privileges are gone for the app role
        as_user(connection, owner)
        for statement in ("UPDATE class_messages SET body = 'x'", "DELETE FROM class_messages"):
            with pytest.raises(ProgrammingError, match="permission denied"):
                connection.execute(text(statement))
            connection.rollback()
            as_user(connection, owner)
    with engine_for(owner_database_url) as engine, engine.begin() as connection:
        as_user(connection, owner)  # FORCE: the table owner is held to the policies too
        assert connection.execute(text("UPDATE class_messages SET body = 'x'")).rowcount == 0
        assert connection.execute(text("DELETE FROM class_messages")).rowcount == 0
    assert count(admin, "class_messages") == 2
    # Deleting a job or a revision only clears the reference.
    with app.begin() as connection:
        as_user(connection, owner)
        connection.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": job_id})
        connection.execute(text("DELETE FROM class_revisions WHERE id = :r"), {"r": rev})
    row = admin.execute(
        text("SELECT job_id, base_revision_id, result_revision_id FROM class_messages "
             "WHERE seq = 1 AND class_id = :c"), {"c": class_1},
    ).one()  # fmt: skip
    assert tuple(row) == (None, None, None)
    with app.begin() as connection:  # the class goes: its messages with it
        as_user(connection, owner)
        connection.execute(text("DELETE FROM classes WHERE id = :c"), {"c": class_1})
    assert count(admin, "class_messages") == 1
    with app.begin() as connection:  # the account goes: all
        connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    assert count(admin, "class_messages") == 0


@pytest.mark.parametrize(
    ("table", "columns", "constraint"),
    [
        ("class_messages", {"role": "system"}, "class_messages_(role|body)"),
        ("class_messages", {"seq": 0}, "class_messages_seq"),
        ("class_messages", {"body": ""}, "class_messages_body"),
        ("class_messages", {"body": "x" * 2001}, "class_messages_body"),
        ("class_messages", {"role": "assistant", "body": "x" * 301}, "class_messages_body"),
        ("class_messages", {"change": {"x": "y" * 1100}}, "class_messages_change"),
        ("saved_workouts", {"program_id": 0}, "saved_workouts_program_id"),
        ("saved_workouts", {"playlist_id": 0}, "saved_workouts_playlist_id"),
        ("saved_workouts", {"revision_number": 0}, "saved_workouts_revision_number"),
        ("saved_workouts", {"content_hash": b"x"}, "saved_workouts_content_hash"),
        ("saved_workouts", {"state_hash": b"x"}, "saved_workouts_state_hash"),
        ("saved_workouts", {"name": ""}, "saved_workouts_name"),
        ("saved_workouts", {"name": "x" * 101}, "saved_workouts_name"),
    ],
)
def test_new_table_checks(
    admin: Connection, table: str, columns: dict[str, Any], constraint: str
) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    make = message if table == "class_messages" else saved
    ok = make(admin, owner, class_id)
    admin.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": ok})  # noqa: S608
    if table == "class_messages":
        row: dict[str, Any] = {"seq": 1, "role": "user", "body": "ok", **columns}
    else:
        row = {
            "program_id": 1, "revision_number": 1, "content_hash": digest("c1"), "name": "ok",
            **columns,
        }  # fmt: skip
    with pytest.raises(IntegrityError, match=constraint):
        insert(admin, table, owner_id=owner, class_id=class_id, **row)


def test_one_live_saved_workout_per_program_and_a_detached_one_frees_it(
    admin: Connection,
) -> None:
    a, b = new_user(admin), new_user(admin)
    class_a, class_a2, class_b = new_class(admin, a), new_class(admin, a), new_class(admin, b)
    first = saved(admin, a, class_a, 41)
    with pytest.raises(IntegrityError, match="saved_workouts_live_program"):
        saved(admin, a, class_a2, 41)
    saved(admin, b, class_b, 41)  # another user's account: the same program id is no conflict
    admin.execute(text("UPDATE saved_workouts SET detached_at = now() WHERE id = :i"), {"i": first})
    saved(admin, a, class_a2, 41)
    saved(admin, a, class_a, 41, detached_at="2026-01-01")  # history may repeat an id


def test_the_pointer_operation_and_columns_of_classes(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    rev = revision(admin, owner, class_id)
    admin.execute(
        text("UPDATE classes SET current_revision_id = :r, brief = '{}', music_source = '{}' "
             "WHERE id = :c"), {"r": rev, "c": class_id},
    )  # fmt: skip
    for column, value in (("brief", 4097), ("music_source", 1025)):
        with pytest.raises(IntegrityError, match=f"classes_{column}"):
            admin.execute(
                text(f"UPDATE classes SET {column} = CAST(:v AS jsonb) WHERE id = :c"),  # noqa: S608
                {"v": json.dumps({"x": "y" * value}), "c": class_id},
            )
    admin.execute(text("DELETE FROM class_revisions WHERE id = :r"), {"r": rev})
    pointer = admin.execute(
        text("SELECT current_revision_id FROM classes WHERE id = :c"), {"c": class_id}
    ).scalar_one()
    assert pointer is None


def test_external_operations_gain_kinds_class_and_categories(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    attach = operation(admin, owner, ATTACH)  # class_id NULL for the attach
    for kind in (SAVE, UPDATE):
        operation(admin, owner, kind, class_id=class_id)
        with pytest.raises(IntegrityError, match="external_operations_class"):
            operation(admin, owner, kind)
    with pytest.raises(IntegrityError, match="external_operations_kind"):
        operation(admin, owner, "mowl_delete_workout", class_id=class_id)
    for category in NEW_CATEGORIES:
        job_id = new_job(admin, owner, state="failed", error_category=category,
                         finished_at="2026-01-01")  # fmt: skip
        admin.execute(
            text("UPDATE external_operations SET state = 'failed', error_category = :c "
                 "WHERE id = :i"), {"c": category, "i": attach},
        )  # fmt: skip
        agent_run(admin, owner, job_id, outcome=category, finished_at="2026-01-01")
    with pytest.raises(IntegrityError, match="agent_runs_outcome"):
        agent_run(admin, owner, None, outcome="program_gone", finished_at="2026-01-01")
    row = admin.execute(
        text("SELECT request_context, pending_at, call_ended_at FROM external_operations "
             "WHERE id = :i"), {"i": attach},
    ).one()  # fmt: skip
    assert tuple(row) == ({}, None, None)
    with pytest.raises(IntegrityError, match="external_operations_request_context"):
        operation(admin, owner, SAVE, class_id=class_id, request_context={"x": "y" * 2100})
    admin.execute(text("DELETE FROM classes WHERE id = :c"), {"c": class_id})
    kinds = admin.execute(text("SELECT kind FROM external_operations")).scalars().all()
    assert kinds == [ATTACH]  # the save and update operations went with their class


def test_a_class_deletion_keeps_usage_and_an_account_deletion_removes_it(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    job_id = new_job(admin, owner, class_id)
    rev = revision(admin, owner, class_id, job_id=job_id)
    message(admin, owner, class_id, job_id=job_id, result_revision_id=rev)
    saved(
        admin,
        owner,
        class_id,
        revision_id=rev,
        operation_id=operation(admin, owner, class_id=class_id),
    )
    run = agent_run(admin, owner, job_id, job_kind="design_draft", price_version="2026-10")
    admin.execute(
        text("INSERT INTO job_events (owner_id, job_id, seq, kind, lease_version) "
             "VALUES (:o, :j, 1, 'queued', 0)"), {"o": owner, "j": job_id},
    )  # fmt: skip
    with app.begin() as connection:
        as_user(connection, owner)
        connection.execute(text("DELETE FROM classes WHERE id = :c"), {"c": class_id})
    for table in (
        "class_revisions", "class_messages", "saved_workouts", "jobs", "job_events",
        "external_operations",
    ):  # fmt: skip
        assert count(admin, table) == 0, table
    kept = admin.execute(text("SELECT id, job_id, job_kind, price_version FROM agent_runs")).one()
    assert tuple(kept) == (run, None, "design_draft", "2026-10")
    with app.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    assert count(admin, "agent_runs") == 0


def test_a_run_can_be_finished_but_a_finished_row_cannot_be_changed_or_deleted(
    admin: Connection, app: Engine, owner_database_url: str
) -> None:
    owner = new_user(admin)
    job_id = new_job(admin, owner)
    run = agent_run(admin, owner, job_id)
    finish = text(
        "UPDATE agent_runs SET input_tokens = 5, outcome = 'submitted', finished_at = now() "
        "WHERE id = :i AND outcome IS NULL"
    )
    with app.begin() as connection:  # the fenced update of ADR 0006 section 6, as the app role
        as_user(connection, owner)
        connection.execute(text("UPDATE agent_runs SET turns = 1 WHERE id = :i"), {"i": run})
        assert connection.execute(finish, {"i": run}).rowcount == 1
    change = text("UPDATE agent_runs SET input_tokens = 99, outcome = 'cancelled'")
    with app.connect() as connection:
        as_user(connection, owner)
        assert connection.execute(change).rowcount == 0
        assert (
            connection.execute(
                text("UPDATE agent_runs SET outcome = NULL, finished_at = NULL")
            ).rowcount
            == 0
        )
        with pytest.raises(ProgrammingError, match="permission denied"):
            connection.execute(text("DELETE FROM agent_runs"))
        connection.rollback()
    with engine_for(owner_database_url) as engine, engine.begin() as connection:
        as_user(connection, owner)  # FORCE: the owner role is held to the policies as well
        assert connection.execute(change).rowcount == 0
        assert connection.execute(text("DELETE FROM agent_runs")).rowcount == 0
    row = admin.execute(text("SELECT input_tokens, outcome FROM agent_runs")).one()
    assert tuple(row) == (5, "submitted")
    # A row with no outcome is still open: it can be updated, and a crashed run's rows stay.
    open_run = agent_run(admin, owner, None)
    with app.begin() as connection:
        as_user(connection, owner)
        assert (
            connection.execute(
                text("UPDATE agent_runs SET turns = 2 WHERE id = :i"), {"i": open_run}
            ).rowcount
            == 1
        )


def test_guard_10_flags_an_unregistered_table_and_column(admin: Connection) -> None:
    def problems() -> list[str]:
        return check_retention(admin, RETENTION, SYSTEM_TABLES)

    assert problems() == []
    admin.execute(text("DROP TABLE IF EXISTS public.t_retention"))
    try:
        for ddl in (
            "CREATE TABLE public.t_retention (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "owner_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE, track_titles jsonb)",
            "CREATE TABLE public.t_retention (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "owner_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE, n integer)",
        ):
            admin.execute(text(ddl))
            assert problems() == [
                "t_retention: owned table with no RETENTION entry (ADR 0007 section 11)"
            ]
            admin.execute(text("DROP TABLE public.t_retention"))
    finally:
        admin.execute(text("DROP TABLE IF EXISTS public.t_retention"))
    # A column added to a registered table without a decision fails too, and so does a stale entry.
    admin.execute(text("ALTER TABLE public.classes ADD COLUMN spotify_titles text"))
    try:
        assert problems() == ["classes.spotify_titles: text column not classified in RETENTION"]
    finally:
        admin.execute(text("ALTER TABLE public.classes DROP COLUMN spotify_titles"))
    stale = {**RETENTION, "gone": RETENTION["classes"]}
    assert check_retention(admin, stale, SYSTEM_TABLES) == [
        "gone: RETENTION entry for a table that is not an owned table"
    ]


def test_guard_10_checks_the_deletion_paths_against_the_cascades(admin: Connection) -> None:
    from dataclasses import replace

    wrong = {
        **RETENTION,
        "agent_runs": replace(RETENTION["agent_runs"], deleted_with=frozenset({"class"})),
    }
    assert check_retention(admin, wrong, SYSTEM_TABLES) == [
        "agent_runs: claims deletion with the class but no cascade reaches it"
    ]
    no_path = {
        **RETENTION,
        "agent_runs": replace(RETENTION["agent_runs"], deleted_with=frozenset()),
    }
    assert check_retention(admin, no_path, SYSTEM_TABLES) == ["agent_runs: no deletion path"]


def test_deleting_the_class_then_the_user_leaves_nothing_in_the_registered_tables(
    admin: Connection, app: Engine
) -> None:
    """Plant a row in every registered table; the registered paths remove them (section 11)."""
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    job_id = new_job(admin, owner, class_id)
    rev = revision(admin, owner, class_id, job_id=job_id)
    message(admin, owner, class_id, job_id=job_id)
    saved(admin, owner, class_id, revision_id=rev)
    operation(admin, owner, class_id=class_id)
    agent_run(admin, owner, job_id)
    admin.execute(
        text("INSERT INTO job_events (owner_id, job_id, seq, kind, lease_version) "
             "VALUES (:o, :j, 1, 'queued', 0)"), {"o": owner, "j": job_id},
    )  # fmt: skip
    insert(admin, "mowl_playlist_links", owner_id=owner, source="spotify",
           source_id="37i9dQZF1DXcBWIGoYBM5M", playlist_id=7)  # fmt: skip
    # M4 (migration 0006). The pool operation rows and the Spotify deletion paths are planted and
    # deleted in test_spotify_forget.py.
    insert(admin, "class_playlists", owner_id=owner, class_id=class_id, revision_id=rev,
           playlist_id="0vvXsWCC9xrXsKd4FyS8kM", spotify_user_id="user-a", name="Tuesday climb",
           state="created")  # fmt: skip
    insert(admin, "spotify_pool", owner_id=owner, spotify_user_id="user-a", consent_version=1,
           playlist_id="37i9dQZF1DXcBWIGoYBM5M")  # fmt: skip
    insert(admin, "spotify_oauth_flows", owner_id=owner, state_hash=digest("s"),
           session_hash=digest("x"), client_id="0123456789abcdef0123456789abcdef",
           redirect_uri="u", verifier="A" * 43, created_at="2998-12-31 23:55",
           expires_at="2999-01-01")  # fmt: skip
    # M5 (migration 0007): an entitlement from a code, its redemption, a credit and its lock row.
    code_id = admin.execute(
        text("INSERT INTO redeem_codes (code_hash, label, plan_id, grant_days, max_redemptions, "
             "expires_at) VALUES (:h, 'beta', 'pro', 30, 5, now() + interval '1 day') "
             "RETURNING id"), {"h": digest("code")},
    ).scalar_one()  # fmt: skip
    entitlement = insert(admin, "entitlements", owner_id=owner, plan_id="pro", source="code",
                         code_id=code_id, note="tester")  # fmt: skip
    insert(admin, "code_redemptions", owner_id=owner, code_id=code_id, entitlement_id=entitlement)
    for table in ("credit_ledger", "quota_periods"):
        extra = {"job_id": job_id, "plan_id": "free", "state": "committed"}
        insert(admin, table, owner_id=owner, period=date.today().replace(day=1),
               **(extra if table == "credit_ledger" else {}))  # fmt: skip
    tables = sorted(RETENTION)
    planted = {t for t in tables if count(admin, t) > 0}
    assert planted == set(tables) - {"connections"}
    with app.begin() as connection:
        as_user(connection, owner)
        connection.execute(text("DELETE FROM classes WHERE id = :c"), {"c": class_id})
    left_after_class = {t for t in tables if count(admin, t) > 0}
    expected = {t for t, e in RETENTION.items() if "class" not in e.deleted_with} - {"connections"}
    assert left_after_class == expected
    with app.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    assert {t for t in tables if count(admin, t) > 0} == set()


def test_the_queue_functions_are_unchanged(admin: Connection, app_database_url: str) -> None:
    from classbeat.db.queue_check import QUEUE_VERSION

    assert QUEUE_VERSION == "classbeat-queue v2"
    assert check_queue(admin) == []


def _admin_engine(database_url: str) -> Engine:
    return create_engine(database_url, isolation_level="AUTOCOMMIT")


def test_upgrade_and_downgrade_on_an_empty_and_a_populated_m3a_database(
    owner_database_url: str, database_url: str
) -> None:
    config = alembic_config()
    admin_engine = _admin_engine(database_url)
    try:
        with migrated(owner_database_url, "0003_connections"):
            command.upgrade(config, "0004_jobs")
            command.upgrade(config, "0005_classes")  # empty
            command.downgrade(config, "0004_jobs")
            with admin_engine.connect() as conn:
                owner, other = new_user(conn), new_user(conn)
                class_id, other_class = new_class(conn, owner), new_class(conn, other)
                job_a = new_job(conn, owner, class_id, kind="design_draft", state="completed",
                                finished_at="2026-01-01", credit_state="committed")  # fmt: skip
                job_b = new_job(conn, other, other_class, kind="design_draft", state="failed",
                                error_category="internal", finished_at="2026-01-01")  # fmt: skip
                open_job = new_job(conn, owner, new_class(conn, owner))
                insert(conn, "class_revisions", owner_id=owner, class_id=class_id, number=1,
                       kind="draft", content={}, content_hash=digest("c1"), music={},
                       provenance={}, job_id=job_a)  # fmt: skip
                for who, job_id in ((owner, job_a), (other, job_b)):
                    insert(conn, "agent_runs", owner_id=who, job_id=job_id, **RUN,
                           outcome="submitted", finished_at="2026-01-01")  # fmt: skip
                insert(conn, "agent_runs", owner_id=owner, job_id=open_job, **RUN)
                insert(conn, "external_operations", owner_id=owner, job_id=job_a, kind=ATTACH,
                       idempotency_key="spotify:x", request_hash=digest("op"), state="succeeded",
                       external_id="7", lease_version=1)  # fmt: skip
            # Finished rows exist: the backfill must not be refused by the policy it precedes.
            command.upgrade(config, "0005_classes")
            with admin_engine.connect() as conn:
                runs = conn.execute(
                    text("SELECT job_kind, price_version, (outcome IS NULL) AS open "
                         "FROM agent_runs ORDER BY open, owner_id = :o"), {"o": owner},
                ).all()  # fmt: skip
                assert sorted(tuple(r) for r in runs) == [
                    ("design_draft", "unknown", False),
                    ("design_draft", "unknown", False),
                    ("test_kind", "unknown", True),
                ]
                pending_at = conn.execute(
                    text("SELECT pending_at = created_at FROM external_operations")
                ).scalar_one()
                assert pending_at is True
                conn.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": job_a})
                assert count(conn, "agent_runs") == 3  # SET NULL, not CASCADE
            # Downgrade with the new shapes present: runs without a job go, the rest stays.
            with admin_engine.connect() as conn:
                owner_id = conn.execute(text("SELECT owner_id FROM jobs LIMIT 1")).scalar_one()
                klass = new_class(conn, owner_id)
                conn.execute(text("UPDATE jobs SET error_category = 'internal' WHERE false"))
                operation(conn, owner_id, class_id=klass)
                new_job(conn, owner_id, klass, state="failed", error_category="program_changed",
                        finished_at="2026-01-01")  # fmt: skip
            command.downgrade(config, "0004_jobs")
            with admin_engine.connect() as conn:
                assert count(conn, "agent_runs") == 2
                assert (
                    conn.execute(text("SELECT count(*) FROM external_operations")).scalar_one() == 1
                )
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM jobs WHERE error_category = 'program_changed'")
                    ).scalar_one()
                    == 0
                )
                columns = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'agent_runs' "
                        "AND column_name IN ('job_kind', 'price_version')"
                    )
                ).all()
                assert columns == []
                assert (
                    conn.execute(
                        text(
                            "SELECT count(*) FROM pg_tables WHERE tablename IN "
                            "('class_messages', 'saved_workouts')"
                        )
                    ).scalar_one()
                    == 0
                )
                state = (
                    conn.execute(
                        text("SELECT relforcerowsecurity FROM pg_class WHERE relname = ANY(:t)"),
                        {"t": ["agent_runs", "jobs", "external_operations"]},
                    )
                    .scalars()
                    .all()
                )
                assert state == [True, True, True]
            command.upgrade(config, "0005_classes")  # and up again over the downgraded data
    finally:
        admin_engine.dispose()
