"""The discard's table lists against the migrated schema (ADR 0039, ``classbeat.jobs.discard``).

Every table with a foreign key to ``classes`` or ``jobs``, and every table with a column that
names a class (``*class_id``), is either one whose row keeps the class (``KEEPING``,
``KEEPING_BY_JOB``) or one whose rows may go with it (``DISCARDED_WITH``). A new table that names
a class therefore fails here until someone decides, in its PR, whether it keeps a class.
"""

import secrets
from collections.abc import Iterator

import pytest
from sqlalchemy import TextClause, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from classbeat.jobs.discard import DISCARDED_WITH, KEEPING, KEEPING_BY_JOB
from jobtools import digest, engine_for, new_user

_REFERENCING = text(
    "SELECT DISTINCT c.conrelid::regclass::text FROM pg_constraint c "
    "WHERE c.contype = 'f' AND c.confrelid IN ('public.classes'::regclass, 'public.jobs'::regclass)"
)
_NAMING = text(
    "SELECT DISTINCT table_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND column_name LIKE '%class_id'"
)
_COLUMNS = text(
    "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
)


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def _names(conn: Connection, statement: TextClause) -> set[str]:
    return {str(name).removeprefix("public.") for name in conn.execute(statement).scalars()}


def test_every_table_that_names_a_class_or_a_job_is_decided(admin: Connection) -> None:
    decided = {t for t, _ in KEEPING} | set(KEEPING_BY_JOB) | DISCARDED_WITH
    found = _names(admin, _REFERENCING) | _names(admin, _NAMING)
    assert {"jobs", "saved_workouts", "external_operations", "player_publications"} <= found
    assert found - decided == set()


def test_the_listed_tables_and_columns_exist(admin: Connection) -> None:
    columns = {(t, c) for t, c in admin.execute(_COLUMNS).all()}
    for table, column in KEEPING:
        assert (table, column) in columns
    for table in KEEPING_BY_JOB:
        assert (table, "job_id") in columns
    for table in DISCARDED_WITH:
        assert any(t == table for t, _ in columns)


_CLASSLESS = text(
    "INSERT INTO jobs (owner_id, kind, state, class_id, input, idempotency_key, request_hash) "
    "VALUES (:o, :k, 'queued', NULL, '{}'::jsonb, :key, :h)"
)


@pytest.mark.parametrize(
    ("kind", "allowed"),
    [
        ("player_remove", True),
        ("spotify_sync", True),
        ("design_draft", False),
        ("test_kind", False),
    ],
)
def test_only_the_classless_kinds_have_no_class_without_a_discard(
    admin: Connection, kind: str, allowed: bool
) -> None:
    """``jobs_class_id`` as migrations 0052, 0056 and 0058 leave it: a job without a class is a
    ``player_remove`` or a ``spotify_sync`` (or a detached discarded job, ``jobs_discarded``)."""
    owner = new_user(admin)
    params = {"o": owner, "k": kind, "key": secrets.token_urlsafe(18), "h": digest("r")}
    if allowed:
        admin.execute(_CLASSLESS, params)
        return
    with pytest.raises(IntegrityError, match="jobs_class_id"):
        admin.execute(_CLASSLESS, params)
