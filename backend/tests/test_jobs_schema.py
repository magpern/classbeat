"""The job tables of ADR 0005 section 1 and ADR 0006 section 6: contract, checks and cascades."""

import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from classbeat.db import SYSTEM_TABLES
from jobtools import as_user, digest, engine_for, job, new_class, new_job, new_user
from rls_guard import check_tables
from role_guard import check_roles

JOB_TABLES = (
    "classes",
    "class_revisions",
    "jobs",
    "job_events",
    "external_operations",
    "mowl_playlist_links",
    "agent_runs",
)
ATTACH = "mowl_attach_spotify_playlist"
PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"


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


def new_revision(
    conn: Connection, owner: uuid.UUID, class_id: uuid.UUID, **columns: Any
) -> uuid.UUID:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "owner_id": owner,
        "class_id": class_id,
        "number": 1,
        "kind": "draft",
        "content_hash": digest("content"),
        **columns,
    }
    names = ", ".join([*values, "content", "music", "provenance"])
    params = ", ".join([f":{k}" for k in values] + ["'{}'", "'{}'", "'{}'"])
    return conn.execute(
        text(f"INSERT INTO class_revisions ({names}) VALUES ({params}) RETURNING id"),  # noqa: S608
        values,
    ).scalar_one()


def test_the_job_tables_meet_guards_1_and_2(admin: Connection) -> None:
    assert check_tables(admin, SYSTEM_TABLES) == []
    assert check_roles(admin) == []
    for table in JOB_TABLES:
        assert table not in SYSTEM_TABLES
    policies = admin.execute(
        text(
            "SELECT tablename, policyname, permissive, cmd, roles::text[], qual FROM pg_policies "
            "WHERE tablename = ANY(:t) AND policyname <> 'owner_isolation'"
        ),
        {"t": list(JOB_TABLES)},
    ).all()
    assert sorted(tuple(p) for p in policies) == [
        (
            "agent_runs",
            "agent_runs_finished_immutable",
            "RESTRICTIVE",
            "UPDATE",
            ["public"],
            "(outcome IS NULL)",
        ),
        ("agent_runs", "agent_runs_no_delete", "RESTRICTIVE", "DELETE", ["public"], "false"),
        ("class_revisions", "revisions_immutable", "RESTRICTIVE", "UPDATE", ["public"], "false"),
        ("job_events", "events_no_delete", "RESTRICTIVE", "DELETE", ["public"], "false"),
        ("job_events", "events_no_update", "RESTRICTIVE", "UPDATE", ["public"], "false"),
    ]


@pytest.mark.parametrize(
    ("table", "policy", "cmd"),
    [
        ("class_revisions", "revisions_immutable", "UPDATE"),
        ("job_events", "events_no_update", "UPDATE"),
        ("job_events", "events_no_delete", "DELETE"),
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


def _event(conn: Connection, owner: uuid.UUID, job_id: uuid.UUID, seq: int = 1) -> None:
    conn.execute(
        text(
            "INSERT INTO job_events (owner_id, job_id, seq, kind, lease_version) "
            "VALUES (:o, :j, :s, 'queued', 0)"
        ),
        {"o": owner, "j": job_id, "s": seq},
    )


def test_job_events_are_append_only_but_cascade_with_job_and_account(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    first, second = new_job(admin, owner), new_job(admin, owner)
    with app.begin() as connection:  # the app role, as the owner, under RLS
        as_user(connection, owner)
        _event(connection, owner, first)
        _event(connection, owner, second)
        assert connection.execute(text("UPDATE job_events SET seq = 9")).rowcount == 0
        assert connection.execute(text("DELETE FROM job_events")).rowcount == 0
        assert connection.execute(text("SELECT count(*) FROM job_events")).scalar_one() == 2
        connection.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": first})
        assert connection.execute(text("SELECT job_id FROM job_events")).scalars().all() == [second]
    with app.begin() as connection:  # account deletion, as classbeat.auth does it
        connection.execute(text("DELETE FROM users WHERE id = :o"), {"o": owner})
    assert admin.execute(text("SELECT count(*) FROM job_events")).scalar_one() == 0


def test_revisions_are_immutable_for_every_role(
    admin: Connection, app: Engine, owner_database_url: str
) -> None:
    owner = new_user(admin)
    revision = new_revision(admin, owner, new_class(admin, owner))
    with app.begin() as connection:
        as_user(connection, owner)
        assert connection.execute(text("SELECT count(*) FROM class_revisions")).scalar_one() == 1
        updated = connection.execute(text("UPDATE class_revisions SET number = 2"))
        assert updated.rowcount == 0
    with engine_for(owner_database_url) as engine, engine.begin() as connection:
        as_user(connection, owner)  # FORCE: the table owner is held to the policies too
        assert connection.execute(text("UPDATE class_revisions SET number = 2")).rowcount == 0
    number = admin.execute(
        text("SELECT number FROM class_revisions WHERE id = :id"), {"id": revision}
    ).scalar_one()
    assert number == 1


def test_deleting_a_job_sets_job_id_null_on_immutable_revisions_and_operations(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    job_id = new_job(admin, owner, class_id)
    revision = new_revision(admin, owner, class_id, job_id=job_id)
    child = new_revision(admin, owner, class_id, number=2, parent_id=revision, job_id=job_id)
    admin.execute(
        text(
            "INSERT INTO external_operations (owner_id, job_id, kind, idempotency_key, "
            "request_hash, state, lease_version) VALUES (:o, :j, :k, :key, :h, 'pending', 1)"
        ),
        {"o": owner, "j": job_id, "k": ATTACH, "key": f"spotify:{PLAYLIST}", "h": digest("op")},
    )
    admin.execute(
        text(
            "INSERT INTO agent_runs (owner_id, job_id, stage, attempt, provider, model, effort, "
            "prompt_version) VALUES (:o, :j, 'design', 1, 'fake', 'm', 'medium', 'v')"
        ),
        {"o": owner, "j": job_id},
    )
    with app.begin() as connection:  # as the user, under RLS, like the application
        as_user(connection, owner)
        assert connection.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": job_id}).rowcount
        rows = connection.execute(
            text("SELECT id, job_id, parent_id FROM class_revisions ORDER BY number")
        ).all()
        assert [tuple(r) for r in rows] == [(revision, None, None), (child, None, revision)]
        operation = connection.execute(text("SELECT job_id FROM external_operations")).one()
        assert operation.job_id is None  # the operation outlives its job
        # Usage outlives its job (migration 0005, ADR 0007 section 10).
        run = connection.execute(text("SELECT job_id FROM agent_runs")).one()
        assert run.job_id is None


def test_deleting_a_revision_sets_parent_and_approval_null(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    parent = new_revision(admin, owner, class_id)
    child = new_revision(admin, owner, class_id, number=2, parent_id=parent)
    job_id = new_job(
        admin, owner, class_id, state="waiting_for_approval", approval_revision_id=parent,
        approval_hash=digest("list"), expires_at="2099-01-01",
    )  # fmt: skip
    admin.execute(text("DELETE FROM class_revisions WHERE id = :id"), {"id": parent})
    assert (
        admin.execute(
            text("SELECT parent_id FROM class_revisions WHERE id = :id"), {"id": child}
        ).scalar_one()
        is None
    )
    assert job(admin, job_id).approval_revision_id is None


def test_deleting_a_class_or_user_cascades(admin: Connection) -> None:
    owner = new_user(admin)
    class_id = new_class(admin, owner)
    job_id = new_job(admin, owner, class_id)
    new_revision(admin, owner, class_id, job_id=job_id)
    admin.execute(
        text(
            "INSERT INTO job_events (owner_id, job_id, seq, kind, lease_version) "
            "VALUES (:o, :j, 1, 'queued', 0)"
        ),
        {"o": owner, "j": job_id},
    )
    admin.execute(text("DELETE FROM classes WHERE id = :id"), {"id": class_id})
    for table in ("jobs", "class_revisions", "job_events"):
        count = admin.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
        assert count == 0, table
    admin.execute(
        text(
            "INSERT INTO mowl_playlist_links (owner_id, source, source_id, playlist_id) "
            "VALUES (:o, 'spotify', :s, 7)"
        ),
        {"o": owner, "s": PLAYLIST},
    )
    admin.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner})
    assert admin.execute(text("SELECT count(*) FROM mowl_playlist_links")).scalar_one() == 0


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ({"attempt": 4, "max_attempts": 3}, "jobs_attempt"),
        ({"attempt": -1}, "jobs_attempt"),
        ({"max_attempts": 11}, "jobs_max_attempts"),
        ({"max_attempts": 0, "attempt": 0}, "jobs_max_attempts"),
        ({"kind": "Bad-Kind"}, "jobs_kind"),
        ({"state": "paused"}, "jobs_state"),
        ({"idempotency_key": "short"}, "jobs_idempotency_key"),
        ({"request_hash": b"x"}, "jobs_request_hash"),
        ({"state": "running"}, "jobs_lease"),
        ({"leased_by": "w1"}, "jobs_lease"),
        ({"state": "waiting_for_approval"}, "jobs_approval"),
        ({"state": "failed", "finished_at": "2026-10-28"}, "jobs_failed"),
        ({"error_category": "internal"}, "jobs_failed"),
        ({"state": "completed"}, "jobs_finished"),
        ({"finished_at": "2026-10-28"}, "jobs_finished"),
        ({"credit_state": "spent"}, "jobs_credit_state"),
        ({"input": '"' + "x" * 16384 + '"'}, "jobs_input"),
    ],
)
def test_the_job_checks_refuse(admin: Connection, columns: dict[str, Any], constraint: str) -> None:
    owner = new_user(admin)
    with pytest.raises(IntegrityError, match=constraint):
        new_job(admin, owner, **columns)


def test_one_open_job_per_class_and_a_new_one_after_it_ended(admin: Connection) -> None:
    owner, other = new_user(admin), new_user(admin)
    class_id = new_class(admin, owner)
    first = new_job(admin, owner, class_id)
    with pytest.raises(IntegrityError, match="jobs_one_open_per_class"):
        new_job(admin, owner, class_id, state="waiting_for_approval",
                approval_hash=digest("l"), expires_at="2099-01-01")  # fmt: skip
    admin.execute(
        text("UPDATE jobs SET state = 'cancelled', finished_at = now() WHERE id = :id"),
        {"id": first},
    )
    new_job(admin, owner, class_id)
    with pytest.raises(IntegrityError, match="jobs_class_fkey"):
        new_job(admin, other, class_id)  # another user's class: the owner-inclusive key


def test_the_idempotency_key_is_unique_per_owner_only(admin: Connection) -> None:
    owner, other = new_user(admin), new_user(admin)
    key = "k" * 20
    new_job(admin, owner, idempotency_key=key)
    new_job(admin, other, idempotency_key=key)
    with pytest.raises(IntegrityError, match="jobs_owner_id_idempotency_key_key"):
        new_job(admin, owner, idempotency_key=key)


def _insert_operation(connection: Connection, owner: uuid.UUID, job_id: uuid.UUID) -> Any:
    return connection.execute(
        text(
            "INSERT INTO external_operations (id, owner_id, job_id, kind, idempotency_key, "
            "request_hash, state, lease_version) VALUES (:id, :o, :j, :k, :key, :h, 'pending', 1) "
            "ON CONFLICT (owner_id, kind, idempotency_key) DO NOTHING RETURNING id"
        ),
        {
            "id": uuid.uuid4(),
            "o": owner,
            "j": job_id,
            "k": ATTACH,
            "key": f"spotify:{PLAYLIST}",
            "h": digest("attach"),
        },
    ).first()


def test_racing_inserts_on_one_operation_key_give_one_row_and_no_error(
    admin: Connection, app: Engine
) -> None:
    """Two jobs of one user attach the same playlist at the same moment (ADR 0005 section 7)."""
    owner = new_user(admin)
    jobs = [new_job(admin, owner), new_job(admin, owner)]
    first_inserted = threading.Event()
    results: dict[int, Any] = {}
    errors: list[BaseException] = []

    def run(i: int) -> None:
        try:
            with app.begin() as connection:
                as_user(connection, owner)
                if i == 1:
                    first_inserted.wait(5)
                results[i] = _insert_operation(connection, owner, jobs[i])
                if i == 0:
                    first_inserted.set()
                    time.sleep(0.5)  # the second insert now waits on this row's lock
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert results[0] is not None
    assert results[1] is None
    rows = admin.execute(text("SELECT job_id, state FROM external_operations")).all()
    assert [tuple(r) for r in rows] == [(jobs[0], "pending")]
    # The loser re-loads the row FOR UPDATE and sees the other job's pending row (operation_busy).
    with app.begin() as connection:
        as_user(connection, owner)
        again = connection.execute(
            text(
                "SELECT job_id, state, lease_version FROM external_operations WHERE owner_id = :o "
                "AND kind = :k AND idempotency_key = :key FOR UPDATE"
            ),
            {"o": owner, "k": ATTACH, "key": f"spotify:{PLAYLIST}"},
        ).one()
        assert (again.job_id, again.state) == (jobs[0], "pending")


def test_a_failed_operation_is_taken_over_by_exactly_one_job(
    admin: Connection, app: Engine
) -> None:
    owner = new_user(admin)
    jobs = [new_job(admin, owner), new_job(admin, owner)]
    with app.begin() as connection:
        as_user(connection, owner)
        _insert_operation(connection, owner, jobs[0])
        connection.execute(text("UPDATE external_operations SET state = 'failed'"))
    barrier = threading.Barrier(len(jobs))
    counts: dict[uuid.UUID, int] = {}
    errors: list[BaseException] = []

    def take_over(job_id: uuid.UUID) -> None:
        try:
            with app.begin() as connection:  # one pooled connection per thread
                as_user(connection, owner)
                barrier.wait()
                counts[job_id] = connection.execute(
                    text(
                        "UPDATE external_operations SET state = 'pending', job_id = :j, "
                        "lease_version = 2 WHERE state = 'failed'"
                    ),
                    {"j": job_id},
                ).rowcount
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=take_over, args=(j,)) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(counts.values()) == [0, 1]
    winner = next(j for j, n in counts.items() if n == 1)
    row = admin.execute(text("SELECT job_id, state FROM external_operations")).one()
    assert (row.job_id, row.state) == (winner, "pending")


def test_the_operation_key_is_per_owner(admin: Connection, app: Engine) -> None:
    for owner in (new_user(admin), new_user(admin)):
        with app.begin() as connection:
            as_user(connection, owner)
            assert _insert_operation(connection, owner, new_job(admin, owner)) is not None


@pytest.mark.parametrize(
    ("sql", "constraint"),
    [
        ("INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, state, "
         "lease_version) VALUES (:o, 'spotify_write', 'k', :h, 'pending', 0)",
         "external_operations_kind"),
        ("INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, state, "
         "lease_version) VALUES (:o, 'mowl_attach_spotify_playlist', 'k', :h, 'succeeded', 0)",
         "external_operations_succeeded"),
        ("INSERT INTO mowl_playlist_links (owner_id, source, source_id, playlist_id) "
         "VALUES (:o, 'spotify', 'short', 1)", "mowl_playlist_links_source_id"),
        ("INSERT INTO mowl_playlist_links (owner_id, source, source_id, playlist_id) "
         "VALUES (:o, 'spotify', '37i9dQZF1DXcBWIGoYBM5M', 0)", "mowl_playlist_links_playlist_id"),
        ("INSERT INTO agent_runs (owner_id, job_id, stage, attempt, provider, model, effort, "
         "prompt_version, outcome) VALUES (:o, gen_random_uuid(), 'design', 1, 'f', 'm', 'e', 'v', "
         "'submitted')", "agent_runs_finished"),
    ],
)  # fmt: skip
def test_the_other_checks_refuse(admin: Connection, sql: str, constraint: str) -> None:
    owner = new_user(admin)
    with pytest.raises(DBAPIError, match=constraint):
        admin.execute(text(sql), {"o": owner, "h": digest("x")})


def test_a_playlist_link_is_inserted_once(admin: Connection, app: Engine) -> None:
    owner = new_user(admin)
    sql = (
        "INSERT INTO mowl_playlist_links (owner_id, source, source_id, playlist_id) "
        "VALUES (:o, 'spotify', :s, :p) ON CONFLICT (owner_id, source, source_id) DO NOTHING "
        "RETURNING id"
    )
    with app.begin() as connection:
        as_user(connection, owner)
        assert connection.execute(text(sql), {"o": owner, "s": PLAYLIST, "p": 7}).first()
        assert connection.execute(text(sql), {"o": owner, "s": PLAYLIST, "p": 8}).first() is None
        assert connection.execute(text("SELECT playlist_id FROM mowl_playlist_links")).scalar() == 7
