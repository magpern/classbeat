"""Helpers for the quota tests (ADR 0011 sections 3 and 4; QT1, #214). Fakes only.

- ``admin``: the superuser on its own autocommit connection (setup and probes only).
- ``factory``: the app role on an engine with a connection per parallel caller, all opened
  before the race, so a caller waits only for the database's locks, never for the pool.
- ``guard``: the ledger trigger as ``0008`` has it and as #276 (``0009_m5_hardening``, still open
  when QT1 was written) tightens it, so the settle runs against both; 0008's body is put back
  byte for byte afterwards (its digest is pinned).
- ``test_plan``: a plan row of the test's own limits, granted to a user; removed afterwards.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dbtools import migration_module
from jobtools import engine_for
from plantools import grant

N = 20
Factory = async_sessionmaker[AsyncSession]
PLAN = "quota_test"

# The app role's settle branch of ``credit_ledger_guard`` as #276 (``0009_m5_hardening``) writes
# it: commit with no reason, release with the job's terminal state, both stamped now().
_SETTLE_0008 = "                AND NEW.state IN ('committed', 'released')\n"
_SETTLE_276 = (
    "                AND ((NEW.state = 'committed' AND NEW.reason IS NULL)\n"
    "                     OR (NEW.state = 'released'\n"
    "                         AND NEW.reason IN ('failed', 'cancelled', 'expired')))\n"
    "                AND NEW.settled_at = now()\n"
)


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
async def factory(app_database_url: str) -> AsyncIterator[Factory]:
    engine = create_async_engine(app_database_url, pool_size=N + 2, max_overflow=0)
    try:
        connections = [await engine.connect() for _ in range(N + 2)]
        for connection in connections:
            await connection.close()  # back to the pool, already open
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(params=["0008", "276"])
def guard(request: pytest.FixtureRequest, admin: Connection) -> Iterator[str]:
    """The ledger trigger of ``0008`` or as #276 tightens it."""
    current: str = migration_module("ledger_guard_owner").LEDGER_GUARD
    if request.param == "0008":
        yield "0008"
        return
    assert current.count(_SETTLE_0008) == 1
    admin.execute(text(current.replace(_SETTLE_0008, _SETTLE_276)))
    try:
        yield "276"
    finally:
        admin.execute(text(current))


@pytest.fixture
def test_plan(admin: Connection) -> Iterator[Callable[..., None]]:
    """``make(owner, **limits)``: the plan row (free's limits unless given), granted to owner."""
    made = False

    def make(owner: UUID, **limits: int) -> None:
        nonlocal made
        values = {
            "classes_per_month": 3,
            "revisions_per_class": 3,
            "concurrent_jobs": 1,
            "monthly_cost_micros": 10_000_000,
            **limits,
        }
        if not made:
            admin.execute(
                text(
                    "INSERT INTO plans (id, name, rank, classes_per_month, revisions_per_class, "
                    "concurrent_jobs, monthly_cost_micros) VALUES (:id, 'QT1 test', 90, "
                    ":classes_per_month, :revisions_per_class, :concurrent_jobs, "
                    ":monthly_cost_micros)"
                ),
                {"id": PLAN, **values},
            )
            made = True
        grant(admin, owner, PLAN)

    try:
        yield make
    finally:
        if made:
            admin.execute(text("DELETE FROM entitlements WHERE plan_id = :p"), {"p": PLAN})
            admin.execute(text("DELETE FROM credit_ledger WHERE plan_id = :p"), {"p": PLAN})
            admin.execute(text("DELETE FROM plans WHERE id = :p"), {"p": PLAN})


class Meeting:
    """Each caller waits here until all ``n`` have arrived or ``wait`` seconds pass: a caller
    that holds a row lock keeps it that long, so without the lock every caller would be inside
    its transaction at once."""

    def __init__(self, n: int, wait: float = 0.25) -> None:
        self.n, self.wait, self.arrived = n, wait, 0
        self.together = asyncio.Event()

    async def __call__(self) -> None:
        self.arrived += 1
        if self.arrived >= self.n:
            self.together.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.together.wait(), self.wait)


def count(conn: Connection, sql: str, **params: Any) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def ledger_states(conn: Connection, owner: UUID) -> list[str]:
    rows = conn.execute(
        text("SELECT state FROM credit_ledger WHERE owner_id = :o ORDER BY state"), {"o": owner}
    )
    return [r.state for r in rows]
