"""M5 security suite, part 2 (#227): refunds per cancelled state, and reservations across a
period boundary (PR #268 review; ADR 0011 section 3). Fakes only.

- A cancel through the jobs API releases the credit exactly once, as a separate case for each
  state a user can cancel from: ``queued`` and ``waiting_for_approval`` end at once; a
  ``running`` job only gets a cancel request, keeps its credit until the worker ends it, and is
  refunded only if it ends ``cancelled`` (a run that completes anyway is committed).
- Twenty parallel reservations straddling a month boundary (half the transactions see the old
  month, half the new) hold exactly the plan's three in each month: the per-month lock row
  serialises each month and the two months never count each other's credits.

Already covered elsewhere and not repeated here: two users' parallel reservations never
interfere (QT1, ``test_quota_ledger.py``); every terminal state settles once under twenty
parallel settles (``test_quota_jobs.py``); maint and the app role cannot change an audit row
(``test_admin_audit.py``, ``test_t92_*``).
"""

import asyncio
import itertools
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.jobs import service
from classbeat.jobs.credits import settle_one
from classbeat.quota import Bounds, MeteredLedger
from classbeat.quota import ledger as quota_ledger
from ctx import make_context
from jobapi import Browser, waiting_job
from jobcore import JobsEnv
from jobtools import new_user
from quotatools import Factory, N, admin, count, factory
from test_quota_jobs import new

__all__ = ["admin", "factory"]  # fixtures

LEDGER = MeteredLedger(Bounds())


@pytest.fixture
def users_plan() -> None:
    """Free (three classes a month, one job at a time): no grant."""
    return None


def credit(conn: Connection, job_id: object) -> tuple[str, str | None, str]:
    """The job's ledger row ``(state, reason)`` and the job's own ``credit_state``."""
    row = conn.execute(
        text(
            "SELECT l.state, l.reason, j.credit_state FROM credit_ledger l "
            "JOIN jobs j ON j.id = l.job_id WHERE l.job_id = :j"
        ),
        {"j": job_id},
    ).one()
    return (row.state, row.reason, row.credit_state)


def held(conn: Connection, owner: UUID) -> int:
    """The month's held credits, as ``MeteredLedger.reserve`` counts them."""
    return count(
        conn,
        "SELECT count(*) FROM credit_ledger WHERE owner_id = :o AND (state = 'committed' "
        "OR (state = 'reserved' AND job_id IS NOT NULL))",
        o=owner,
    )


async def settle_twice(env: Any, owner: UUID, job_id: UUID) -> list[str]:
    """Settle the job from twenty callers at once: exactly one may change the row."""
    results = await asyncio.gather(
        *(settle_one(env.factory, LEDGER, owner, job_id) for _ in range(N))
    )
    return sorted((r or "" for r in results), reverse=True)


async def created(browser: Browser) -> UUID:
    response = await browser.create()
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def cancel(browser: Browser, job_id: UUID) -> str:
    response = await browser.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 200, response.text
    return response.json()["state"]


async def test_cancelling_a_queued_job_refunds_its_credit_once(
    admin: Connection, browsers: Any, env: Any
) -> None:
    alice: Browser = await browsers()
    job_id = await created(alice)
    assert credit(admin, job_id) == ("reserved", None, "reserved")
    assert await cancel(alice, job_id) == "cancelled"
    assert await settle_twice(env, alice.user_id, job_id) == ["released"] + [""] * (N - 1)
    assert credit(admin, job_id) == ("released", "cancelled", "released")
    assert held(admin, alice.user_id) == 0


async def test_cancelling_a_waiting_job_refunds_its_credit_once(
    admin: Connection, browsers: Any, env: Any, jenv: JobsEnv
) -> None:
    alice: Browser = await browsers()
    job_id, _ = await waiting_job(jenv, alice)
    assert credit(admin, job_id) == ("reserved", None, "reserved")
    assert await cancel(alice, job_id) == "cancelled"
    assert await settle_twice(env, alice.user_id, job_id) == ["released"] + [""] * (N - 1)
    assert credit(admin, job_id) == ("released", "cancelled", "released")
    assert held(admin, alice.user_id) == 0


def start(conn: Connection, job_id: UUID) -> None:
    """The job claimed by a worker (as ``claim_job`` leaves it)."""
    conn.execute(
        text(
            "UPDATE jobs SET state = 'running', attempt = 1, leased_by = 'other-worker', "
            "lease_expires_at = now() + interval '5 minutes', started_at = now() WHERE id = :j"
        ),
        {"j": job_id},
    )


def end(conn: Connection, job_id: UUID, state: str) -> None:
    """The worker ends the run at its next checkpoint."""
    conn.execute(
        text(
            "UPDATE jobs SET state = :s, finished_at = now(), leased_by = NULL, "
            "lease_expires_at = NULL WHERE id = :j"
        ),
        {"s": state, "j": job_id},
    )


@pytest.mark.parametrize(("ends", "settled", "reason"), [
    ("cancelled", "released", "cancelled"),
    ("completed", "committed", None),  # the cancel came too late: the class was made
])  # fmt: skip
async def test_cancelling_a_running_job_keeps_its_credit_until_the_run_ends(
    admin: Connection, browsers: Any, env: Any, ends: str, settled: str, reason: str | None
) -> None:
    alice: Browser = await browsers()
    job_id = await created(alice)
    start(admin, job_id)
    assert await cancel(alice, job_id) == "running"  # a request; the worker decides
    requested = admin.execute(
        text("SELECT cancel_requested_at IS NOT NULL FROM jobs WHERE id = :j"), {"j": job_id}
    ).scalar_one()
    assert requested is True
    # Nothing to settle while it runs: the credit stays held and counts.
    assert await settle_twice(env, alice.user_id, job_id) == [""] * N
    assert credit(admin, job_id) == ("reserved", None, "reserved")
    assert held(admin, alice.user_id) == 1
    end(admin, job_id, ends)
    assert await settle_twice(env, alice.user_id, job_id) == [settled] + [""] * (N - 1)
    assert credit(admin, job_id) == (settled, reason, settled)
    assert held(admin, alice.user_id) == (1 if settled == "committed" else 0)


# --- a period boundary under twenty parallel reservations ------------------------------------


async def test_parallel_reservations_across_a_month_boundary_hold_three_per_month(
    admin: Connection, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transactions that start before midnight UTC reserve in the old month, the ones
    after it in the new one: ``quota_periods`` gives each month its own lock row, so each
    month holds exactly three, and the old month's three never block the new month's."""
    owner = new_user(admin)  # free: 3 classes a month
    old, new_month = date(2026, 10, 1), date(2026, 11, 1)
    turns = itertools.cycle([old, new_month])
    real_period = quota_ledger.period

    async def straddling(db: Any) -> date:
        await real_period(db)  # the same query, so the timing stays the real one
        return next(turns)

    monkeypatch.setattr(quota_ledger, "period", straddling)
    ctx = make_context(owner)
    results = await asyncio.gather(
        *(service.create(factory, ctx, new(), limit=N, ledger=LEDGER) for _ in range(N))
    )
    assert len([r for r in results if r.created]) == 6
    refused = [r for r in results if not r.created]
    assert {(r.reason, r.counts["quota"], r.counts["used"]) for r in refused} == {
        ("quota", "classes", 3)
    }
    assert {r.counts["period_end"] for r in refused} <= {"2026-11-01", "2026-12-01"}
    by_month = admin.execute(
        text(
            "SELECT period, count(*) FROM credit_ledger WHERE owner_id = :o AND state = "
            "'reserved' GROUP BY period ORDER BY period"
        ),
        {"o": owner},
    ).all()
    assert [tuple(r) for r in by_month] == [(old, 3), (new_month, 3)]
    locks = admin.execute(
        text("SELECT period FROM quota_periods WHERE owner_id = :o ORDER BY period"),
        {"o": owner},
    ).scalars()
    assert list(locks) == [old, new_month]
