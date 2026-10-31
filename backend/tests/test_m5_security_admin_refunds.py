"""M5 security suite, part 2 (#227): the admin refund and the Jobs view's caps under the PR #281
(AD3) review's items.

- Parallel refunds of one committed credit: exactly one 200, the rest 409 ``already_refunded``,
  one ``ok`` row and a ``refused`` row each; the period's held count drops by one, once.
- A refund racing a settle waits for the settle's row lock and answers from the settled state:
  a credit the settle commits is refunded; one it releases is 409 ``not_committed``.
- The 409's reason comes from the row the refund locked (one ``SELECT ... FOR UPDATE``, no
  second read and no ``UPDATE`` for a credit that is not committed).
- The Jobs view's waiting approvals and uncertain operations stop at 200 and say so
  (``waiting_truncated``, ``uncertain_truncated``); exactly 200 is not truncated.

Threats covered: T86, T96.
"""

import asyncio
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, make_url

from adminviews import audit, ledger_row, new_class, new_job, new_ledger, reason, stepped_admin
from authtools import Env, running_env
from classbeat.admin.jobs import LIST_ROWS
from classbeat.db import user_session
from classbeat.quota import Bounds, MeteredLedger
from ctx import make_context

N = 8
LEDGER = MeteredLedger(Bounds())


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def refund_path(owner: uuid.UUID, ledger_id: uuid.UUID) -> str:
    return f"/api/admin/users/{owner}/credits/{ledger_id}/refund"


def held(conn: Connection, owner: uuid.UUID) -> int:
    return int(
        conn.execute(
            text(
                "SELECT count(*) FROM credit_ledger WHERE owner_id = :o AND (state = 'committed' "
                "OR (state = 'reserved' AND job_id IS NOT NULL))"
            ),
            {"o": owner},
        ).scalar_one()
    )


async def test_parallel_refunds_of_one_credit_refund_it_once(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """Threats: T86, T96."""
    actor = await stepped_admin(env, make_actor)
    rider = await env.user("rider@example.com")
    job = new_job(superuser, rider, "design_draft", "completed", credit_state="committed")
    credit = new_ledger(superuser, rider, job, "committed")
    assert held(superuser, rider) == 1
    responses = await asyncio.gather(*(actor.post(refund_path(rider, credit)) for _ in range(N)))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] + [409] * (N - 1), [r.text for r in responses]
    assert {reason(r) for r in responses if r.status_code == 409} == {"already_refunded"}
    (ok,) = [r.json() for r in responses if r.status_code == 200]
    assert (ok["state"], ok["classes_held"]) == ("refunded", 0)
    row = ledger_row(superuser, credit)
    assert (row.state, row.reason, row.refunded_by) == ("refunded", "admin_refund", actor.user_id)
    assert held(superuser, rider) == 0
    rows = audit(superuser, actor.user_id)
    assert sorted(rows) == sorted(
        [("refund.credit", "ok", None)]
        + [("refund.credit", "refused", "already_refunded")] * (N - 1)
    )


async def wait_for_maint_lock(conn: Connection, seconds: float = 10) -> None:
    """Until a ``classbeat_maint`` backend waits on a row lock (the refund behind the settle)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        waiting = conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = 'classbeat_maint' "
                "AND wait_event_type = 'Lock'"
            )
        ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the refund never waited for the settle's row lock")


@pytest.mark.parametrize(("ends", "status", "expected"), [
    ("completed", 200, "refunded"),  # the settle commits: the refund then refunds it
    ("cancelled", 409, "released"),  # the settle releases: nothing to refund
])  # fmt: skip
async def test_a_refund_racing_a_settle_answers_from_the_settled_row(
    env: Env, make_actor: Any, superuser: Connection, ends: str, status: int, expected: str
) -> None:
    """Threats: T86, T96."""
    actor = await stepped_admin(env, make_actor)
    rider = await env.user("rider@example.com")
    job = new_job(superuser, rider, "design_draft", ends, credit_state="reserved")
    credit = new_ledger(superuser, rider, job, "reserved")
    ctx = make_context(rider)
    async with user_session(env.factory, ctx) as db:  # the worker's settle, held open
        if ends == "completed":
            await LEDGER.commit(db, ctx, job)
        else:
            await LEDGER.release(db, ctx, job)
        refund = asyncio.create_task(actor.post(refund_path(rider, credit)))
        await wait_for_maint_lock(superuser)
        assert not refund.done()
    response = await refund
    assert response.status_code == status, response.text
    if status == 409:
        assert reason(response) == "not_committed"
    assert ledger_row(superuser, credit).state == expected
    outcome = ("refund.credit", "ok", None) if status == 200 else (
        "refund.credit", "refused", "not_committed"
    )  # fmt: skip
    assert audit(superuser, actor.user_id) == [outcome]


@pytest.mark.parametrize(("state", "expected"), [
    ("reserved", "not_committed"), ("released", "not_committed"), ("refunded", "already_refunded"),
])  # fmt: skip
async def test_the_409_reason_comes_from_the_one_locked_read(
    env: Env, make_actor: Any, superuser: Connection, state: str, expected: str
) -> None:
    """One ``SELECT ... FOR UPDATE`` of the row decides; no ``UPDATE`` is tried and no second
    read happens for a credit that is not committed (PR #281 review).

    Threats: T96.
    """
    actor = await stepped_admin(env, make_actor)
    rider = await env.user("rider@example.com")
    job_credit = "committed" if state == "refunded" else state  # the job keeps its settle
    job = new_job(superuser, rider, "design_draft", "cancelled", credit_state=job_credit)
    credit = new_ledger(
        superuser, rider, job, state,
        reason={"released": "cancelled", "refunded": "admin_refund"}.get(state),
    )  # fmt: skip
    statements: list[str] = []
    engine = env.app.state.admin_db._maint_engine.sync_engine  # pyright: ignore[reportPrivateUsage]

    def seen(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if "credit_ledger" in statement:
            statements.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", seen)
    try:
        response = await actor.post(refund_path(rider, credit))
    finally:
        event.remove(engine, "before_cursor_execute", seen)
    assert (response.status_code, reason(response)) == (409, expected)
    assert len(statements) == 1, statements
    assert statements[0].startswith("SELECT state FROM credit_ledger")
    assert statements[0].endswith("FOR UPDATE")
    assert ledger_row(superuser, credit).state == state


# --- the Jobs view's 200-row caps -------------------------------------------------------------


def plant_waiting_and_uncertain(
    conn: Connection, owner: uuid.UUID, n: int, uncertain: int | None = None
) -> None:
    for _ in range(n):
        new_job(conn, owner, "design_with_songs", "waiting_for_approval", credit_state="reserved")
    n = n if uncertain is None else uncertain
    class_id = new_class(conn, owner)
    conn.execute(
        text(
            "INSERT INTO external_operations (owner_id, class_id, kind, idempotency_key, "
            "request_hash, state, lease_version) SELECT :o, :c, 'spotify_create_playlist', "
            "'cap-' || g || '-' || :t, decode(md5(g::text || :t), 'hex') || "
            "decode(md5(g::text || :t || 'x'), 'hex'), 'uncertain', 1 "
            "FROM generate_series(1, :n) AS g"
        ),
        {"o": owner, "c": class_id, "n": n, "t": secrets.token_hex(4)},
    )


@pytest.mark.parametrize(("n", "truncated"), [(LIST_ROWS, False), (LIST_ROWS + 1, True)])
async def test_the_jobs_view_says_when_a_list_hit_its_cap(
    env: Env, make_actor: Any, superuser: Connection, n: int, truncated: bool
) -> None:
    """Threats: T86 (the jobs a refund decision needs)."""
    actor = await stepped_admin(env, make_actor)
    rider = await env.user("rider@example.com")
    plant_waiting_and_uncertain(superuser, rider, n)
    response = await actor.get("/api/admin/jobs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (len(body["waiting_approvals"]), body["waiting_truncated"]) == (LIST_ROWS, truncated)
    assert (len(body["uncertain_operations"]), body["uncertain_truncated"]) == (
        LIST_ROWS,
        truncated,
    )
    states = {s["state"]: s["count"] for s in body["states"]}
    assert states["waiting_for_approval"] == n  # the counts are never capped


@pytest.mark.parametrize(("waiting", "uncertain"), [
    (LIST_ROWS + 1, LIST_ROWS), (LIST_ROWS, LIST_ROWS + 1),
])  # fmt: skip
async def test_each_truncation_flag_follows_its_own_list(
    env: Env, make_actor: Any, superuser: Connection, waiting: int, uncertain: int
) -> None:
    """#303 delta review, note 2: one list over the cap and the other exactly at it, both ways,
    so a flag computed from the wrong list (or from both) fails.

    Threats: T86 (the jobs a refund decision needs).
    """
    actor = await stepped_admin(env, make_actor)
    rider = await env.user("rider@example.com")
    plant_waiting_and_uncertain(superuser, rider, waiting, uncertain=uncertain)
    body = (await actor.get("/api/admin/jobs")).json()
    assert (body["waiting_truncated"], body["uncertain_truncated"]) == (
        waiting > LIST_ROWS,
        uncertain > LIST_ROWS,
    )
    assert len(body["waiting_approvals"]) == len(body["uncertain_operations"]) == LIST_ROWS
