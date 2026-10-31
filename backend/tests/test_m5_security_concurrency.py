"""M5 security suite, concurrency (ADR 0011 section 10; SEC1, #227). Fakes only.

Every race here runs on **separate database connections at once**: the async ones on an engine
with a connection per caller (so no caller waits for the pool, only for the database's locks),
the sync ones on threads that meet at a barrier before their statement. Each test names the
threat of ``docs/security/threat-model.md`` it covers.

- T88: parallel redeems of a code's last use give one entitlement; parallel redeems of a code
  with uses for everyone lose no count; one user's parallel redeems give one; two codes' races
  don't interfere.
- T89: parallel trials give one per account, and one user's trial never uses another's.
- T96: parallel settles of one ledger row change it once; an admin refund racing the settle
  never refunds a live reservation; the job's settle step (``classbeat.jobs.credits``) settles
  each terminal state once through ``classbeat.quota.MeteredLedger``, with the ledger and
  ``credit_state`` agreeing.

Quota reservation under concurrency (T85: 20 reservations against a limit of 3, the period's
held count, creates and approvals against ``concurrent_jobs``) is QT1's (#214):
``test_quota_ledger.py``, ``test_quota_jobs.py`` and ``test_quota_api.py``.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classbeat.jobs.credits import settle_one
from classbeat.plans.account import account_plan
from classbeat.plans.redeem import Redeemed, redeem_code
from classbeat.plans.trial import TrialStarted, start_trial
from classbeat.quota import Bounds, MeteredLedger
from ctx import make_context
from jobtools import as_user, engine_for, new_job, new_user
from plantools import plant_code, redeemed_count, set_trial
from test_plans_schema import MAINT, REFUSED, ledger, row

N = 20
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    """The superuser (setup and probes only); no ``trial`` setting before or after."""
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        connection.execute(text("DELETE FROM app_settings"))
        try:
            yield connection
        finally:
            connection.execute(text("DELETE FROM app_settings"))


@pytest.fixture
async def factory(app_database_url: str) -> AsyncIterator[Factory]:
    """The app role with one connection per parallel caller, opened before the race."""
    engine = create_async_engine(app_database_url, pool_size=N + 2, max_overflow=0)
    try:
        connections = [await engine.connect() for _ in range(N + 2)]
        for connection in connections:
            await connection.close()  # back to the pool, already open
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url, pool_size=N + 2, max_overflow=0) as engine:
        yield engine


def entitlements(conn: Connection, **where: Any) -> int:
    clause = " AND ".join(f"{k} = :{k}" for k in where)
    return conn.execute(
        text(f"SELECT count(*) FROM entitlements WHERE {clause}"),  # noqa: S608
        where,
    ).scalar_one()


def race(calls: list[Callable[[], Any]]) -> list[Any]:
    """Run each call on its own thread, all released at once; their results or exceptions."""
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def run(index: int) -> None:
        barrier.wait()
        try:
            results[index] = calls[index]()
        except Exception as error:  # the test inspects it
            results[index] = error

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


# --- T88: redeem codes ------------------------------------------------------------------------


async def test_t88_parallel_redeems_of_the_last_use_give_one_entitlement(
    admin: Connection, factory: Factory
) -> None:
    users = [new_user(admin) for _ in range(N)]
    code, code_id = plant_code(admin, uses=5, used=4)
    results = await asyncio.gather(*(redeem_code(factory, make_context(u), code) for u in users))
    won = [r for r in results if isinstance(r, Redeemed)]
    assert len(won) == 1, results
    assert [r for r in results if not isinstance(r, Redeemed)] == ["code_invalid"] * (N - 1)
    assert redeemed_count(admin, code_id) == 5
    assert entitlements(admin, code_id=code_id) == 1
    assert (
        admin.execute(
            text("SELECT count(*) FROM code_redemptions WHERE code_id = :c"), {"c": code_id}
        ).scalar_one()
        == 1
    )


async def test_t88_parallel_redeems_with_uses_for_all_lose_no_count(
    admin: Connection, factory: Factory
) -> None:
    users = [new_user(admin) for _ in range(N)]
    code, code_id = plant_code(admin, uses=N)
    results = await asyncio.gather(*(redeem_code(factory, make_context(u), code) for u in users))
    assert all(isinstance(r, Redeemed) for r in results), results
    assert redeemed_count(admin, code_id) == N
    assert entitlements(admin, code_id=code_id) == N
    late = new_user(admin)  # every use is gone now
    assert await redeem_code(factory, make_context(late), code) == "code_invalid"
    assert redeemed_count(admin, code_id) == N


async def test_t88_one_users_parallel_redeems_give_one_entitlement(
    admin: Connection, factory: Factory
) -> None:
    user = new_user(admin)
    code, code_id = plant_code(admin, uses=N)
    ctx = make_context(user)
    results = await asyncio.gather(*(redeem_code(factory, ctx, code) for _ in range(N)))
    assert len([r for r in results if isinstance(r, Redeemed)]) == 1
    assert [r for r in results if not isinstance(r, Redeemed)] == ["already_redeemed"] * (N - 1)
    assert redeemed_count(admin, code_id) == 1
    assert entitlements(admin, owner_id=user) == 1


async def test_t88_two_codes_raced_at_once_never_interfere(
    admin: Connection, factory: Factory
) -> None:
    users = [new_user(admin) for _ in range(N)]
    first, first_id = plant_code(admin, plan="instructor", uses=1)
    second, second_id = plant_code(admin, plan="pro", uses=1)
    calls = [
        redeem_code(factory, make_context(u), first if i % 2 else second)
        for i, u in enumerate(users)
    ]
    results = await asyncio.gather(*calls)
    won = [r for r in results if isinstance(r, Redeemed)]
    assert sorted(str(r.plan_id) for r in won) == ["instructor", "pro"]
    assert (redeemed_count(admin, first_id), redeemed_count(admin, second_id)) == (1, 1)
    assert (entitlements(admin, code_id=first_id), entitlements(admin, code_id=second_id)) == (1, 1)


# --- T89: trials ------------------------------------------------------------------------------


async def test_t89_parallel_trials_give_one_per_account_and_never_cross_accounts(
    admin: Connection, factory: Factory
) -> None:
    set_trial(admin, {"plan": "instructor", "days": 14})
    alice, bob = new_user(admin), new_user(admin)
    contexts = [make_context(alice if i % 2 else bob) for i in range(N)]
    results = await asyncio.gather(*(start_trial(factory, c, 30) for c in contexts))
    started = [r for r in results if isinstance(r, TrialStarted)]
    assert len(started) == 2, results
    assert [r for r in results if not isinstance(r, TrialStarted)] == ["trial_used"] * (N - 2)
    for user in (alice, bob):
        assert entitlements(admin, owner_id=user, source="trial") == 1


# --- T96: the ledger under parallel writers ----------------------------------------------------

# No state condition of its own: the restrictive policy's ``USING (state = 'reserved')``,
# re-checked on the row a concurrent settle left behind, is what makes the losers update nothing.
SETTLE = "UPDATE credit_ledger SET state = :s, reason = :r, settled_at = now() WHERE id = :i"
REFUND = (
    "UPDATE credit_ledger SET state = 'refunded', reason = 'admin_refund', settled_at = now(), "
    "refunded_by = :by WHERE id = :i"
)


def settle_as_app(app: Engine, user: UUID, entry: UUID, state: str) -> int:
    with app.begin() as connection:
        as_user(connection, user)
        reason = None if state == "committed" else "failed"
        return connection.execute(text(SETTLE), {"s": state, "r": reason, "i": entry}).rowcount


def test_t96_parallel_settles_of_one_ledger_row_change_it_once(
    admin: Connection, app: Engine
) -> None:
    user = new_user(admin)
    entry = ledger(admin, user, new_job(admin, user))
    states = ["committed" if i % 2 else "released" for i in range(N)]
    results = race([lambda s=s: settle_as_app(app, user, entry, s) for s in states])
    assert not [r for r in results if isinstance(r, Exception)], results
    assert sorted(results) == [0] * (N - 1) + [1]
    winner = states[results.index(1)]
    settled = row(admin, entry)
    assert (settled.state, settled.settled_at is not None) == (winner, True)


@pytest.fixture
def maint_engine(admin: Connection, database_url: str) -> Iterator[Engine]:
    """A stand-in for ``classbeat_maint`` (AR1, #257, creates the real one) with the ledger
    columns ADR 0011 section 5 grants it, reached by ``SET ROLE`` on each connection."""
    created = not admin.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": MAINT}
    ).first()
    if created:
        admin.execute(text(f"CREATE ROLE {MAINT} NOLOGIN NOSUPERUSER BYPASSRLS NOINHERIT"))
        admin.execute(text(f"GRANT USAGE ON SCHEMA public TO {MAINT}"))
        admin.execute(
            text(
                f"GRANT SELECT, UPDATE (state, reason, settled_at, refunded_by) "
                f"ON public.credit_ledger TO {MAINT}"
            )
        )
    try:
        with engine_for(database_url, pool_size=4, max_overflow=0) as engine:
            yield engine
    finally:
        if created:
            admin.execute(text(f"REVOKE ALL ON public.credit_ledger FROM {MAINT}"))
            admin.execute(text(f"REVOKE ALL ON SCHEMA public FROM {MAINT}"))
            admin.execute(text(f"DROP ROLE {MAINT}"))


def refund_as_maint(maint: Engine, entry: UUID, by: UUID) -> int:
    with maint.begin() as connection:
        connection.execute(text(f"SET LOCAL ROLE {MAINT}"))
        return connection.execute(text(REFUND), {"i": entry, "by": by}).rowcount


@pytest.mark.parametrize("settled_as", ["committed", "released"])
def test_t96_an_admin_refund_racing_the_settle_never_refunds_a_live_reservation(
    admin: Connection, app: Engine, maint_engine: Engine, settled_as: str
) -> None:
    for _ in range(5):  # either order may win; each round checks the outcome of that order
        user = new_user(admin)
        entry = ledger(admin, user, new_job(admin, user))
        settle, refund = race(
            [
                lambda u=user, e=entry: settle_as_app(app, u, e, settled_as),
                lambda u=user, e=entry: refund_as_maint(maint_engine, e, u),
            ]
        )
        assert settle == 1  # the app's settle always lands: the refund can't take the row first
        final = row(admin, entry)
        if isinstance(refund, DBAPIError):
            assert REFUSED in str(refund)
            assert final.state == settled_as
        else:  # the refund ran after the commit and saw it
            assert (settled_as, refund, final.state, final.refunded_by) == (
                "committed",
                1,
                "refunded",
                user,
            )


@pytest.mark.parametrize(
    ("state", "columns", "expected"),
    [
        ("completed", {}, "committed"),
        ("failed", {"error_category": "upstream_unavailable"}, "released"),
        ("failed", {"error_category": "lease_lost"}, "released"),
        ("cancelled", {}, "released"),
        ("expired", {}, "released"),
    ],
)
async def test_t96_each_terminal_state_settles_once_with_the_ledger_and_job_agreeing(
    admin: Connection,
    factory: Factory,
    state: str,
    columns: dict[str, Any],
    expected: str,
) -> None:
    user = new_user(admin)
    job_id = new_job(admin, user, state=state, finished_at=datetime.now(UTC), **columns)
    entry = ledger(admin, user, job_id)
    other_user = new_user(admin)
    kept = ledger(admin, other_user, new_job(admin, other_user))  # another user's live credit
    metered = MeteredLedger(Bounds())
    reason = state if state != "completed" else None  # how the job ended
    ctx = make_context(user)
    before = await account_plan(factory, ctx, bounds=Bounds(), trial_days_max=30)
    assert before.classes_held == 1
    results = await asyncio.gather(*(settle_one(factory, metered, user, job_id) for _ in range(N)))
    assert sorted(r or "" for r in results) == [""] * (N - 1) + [expected]
    credit_state = admin.execute(
        text("SELECT credit_state FROM jobs WHERE id = :j"), {"j": job_id}
    ).scalar_one()
    settled = row(admin, entry)
    assert (credit_state, settled.state) == (expected, expected)
    assert settled.reason == reason
    after = await account_plan(factory, ctx, bounds=Bounds(), trial_days_max=30)
    # A released credit frees its slot at once; a committed one keeps counting.
    assert after.classes_held == (1 if expected == "committed" else 0)
    assert row(admin, kept).state == "reserved"
