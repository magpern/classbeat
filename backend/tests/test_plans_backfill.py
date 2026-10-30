"""Migration 0007's backfill (ADR 0011 section 1; issue DB1, #207): every open job holding a
reserved credit when M5 deploys gets a ``reserved`` ledger row (the UTC month of its creation,
plan ``free``), per owner with ``app.user_id`` set; nothing else gets one. Then the downgrade."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.db import SYSTEM_TABLES
from dbtools import alembic_config, migrated, tiers_migration
from jobtools import digest, engine_for, new_job, new_user
from rls_guard import check_tables


def tiers_revision() -> tuple[str, str]:
    """(revision, down_revision) of the tiers migration, whatever it is chained onto."""
    module = tiers_migration()
    return module.revision, module.down_revision


@pytest.fixture
def before(owner_database_url: str, database_url: str) -> Iterator[Connection]:
    """The schema just before the tiers migration, and a superuser connection to plant jobs."""
    revision, down = tiers_revision()
    assert ScriptDirectory.from_config(alembic_config()).get_revision(revision) is not None
    with (
        migrated(owner_database_url, down),
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


def open_job(conn: Connection, owner: UUID, state: str, **c: Any) -> UUID:
    extra: dict[str, Any] = {
        "running": {"leased_by": "w1", "lease_expires_at": datetime.now(UTC)},
        "waiting_for_approval": {"approval_hash": digest("a"), "expires_at": datetime.now(UTC)},
    }.get(state, {})
    if state in ("completed", "cancelled", "failed", "expired"):
        extra = {"finished_at": datetime.now(UTC)}
    if state == "failed":
        extra["error_category"] = "internal"
    return new_job(conn, owner, state=state, **extra, **c)


def ledger_rows(conn: Connection) -> set[tuple[Any, ...]]:
    rows = conn.execute(
        text(
            "SELECT owner_id, job_id, to_char(period, 'YYYY-MM-DD'), plan_id, state "
            "FROM credit_ledger WHERE settled_at IS NULL AND reason IS NULL "
            "AND refunded_by IS NULL"
        )
    ).all()
    assert len(rows) == conn.execute(text("SELECT count(*) FROM credit_ledger")).scalar_one()
    return {tuple(r) for r in rows}


def test_open_reserved_jobs_of_two_users_get_a_reserved_row_and_nothing_else(
    before: Connection, owner_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = new_user(before), new_user(before)
    this_month = datetime.now(UTC).strftime("%Y-%m-01")
    expected = {
        (a, open_job(before, a, "queued"), this_month, "free", "reserved"),
        (a, open_job(before, a, "running"), this_month, "free", "reserved"),
        (a, open_job(before, a, "waiting_for_approval"), this_month, "free", "reserved"),
        # The UTC month of its creation: 1 August 00:30 in Stockholm is still July in UTC.
        (
            b,
            open_job(before, b, "queued", created_at="2026-08-01T00:30:00+02:00"),
            "2026-07-01",
            "free",
            "reserved",
        ),
        (b, open_job(before, b, "waiting_for_approval"), this_month, "free", "reserved"),
    }
    # Ended or never reserved: no row.
    open_job(before, a, "queued", credit_state="none")
    open_job(before, a, "completed")  # ended, waiting for settle: settles as a pre-M5 job
    open_job(before, b, "cancelled")
    open_job(before, b, "failed")
    open_job(before, b, "expired")
    open_job(before, b, "completed", credit_state="committed")
    open_job(before, b, "running", credit_state="released")

    monkeypatch.setenv("CLASSBEAT_MIGRATIONS_DATABASE_URL", owner_database_url)
    command.upgrade(alembic_config(), tiers_revision()[0])
    assert ledger_rows(before) == expected
    assert check_tables(before, SYSTEM_TABLES) == []

    # The downgrade removes the M5 tables and functions; up again backfills the same rows.
    command.downgrade(alembic_config(), tiers_revision()[1])
    for name in ("plans", "credit_ledger", "admin_audit"):
        assert before.execute(text("SELECT to_regclass(:t)"), {"t": name}).scalar_one() is None
    assert not before.execute(
        text("SELECT count(*) FROM pg_proc WHERE proname LIKE '%_guard'")
    ).scalar_one()
    command.upgrade(alembic_config(), tiers_revision()[0])
    assert ledger_rows(before) == expected
