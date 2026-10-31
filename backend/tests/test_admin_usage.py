"""The admin Usage and cost view and the admin refund (ADR 0011 sections 3 and 6, AD3 #221): the
gate on every route, one audit row per call, totals that equal the planted runs and ledger rows of
two users (released jobs in cost, not in classes), and the refund: ``committed -> refunded`` frees
a slot at once, every other refund is 409 or 404 and changes nothing, and the ledger trigger
refuses maint a live reservation."""

import secrets
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError

from adminviews import (
    audit,
    ledger_row,
    new_job,
    new_ledger,
    new_run,
    reason,
    stepped_admin,
)
from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems
from classbeat.agent.prices import PRICES_VERSION

USAGE = "/api/admin/usage"


def refund_path(user_id: object, ledger_id: object) -> str:
    return f"/api/admin/users/{user_id}/credits/{ledger_id}/refund"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


@pytest.fixture
def maint(maint_database_url: str) -> Iterator[Connection]:
    """A plain connection as ``classbeat_maint`` (outside ``admin_session``), for the trigger."""
    engine = create_engine(maint_database_url)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


async def test_the_router_guard_covers_the_usage_and_refund_routes(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {USAGE, "/api/admin/users/{user_id}/credits/{ledger_id}/refund"} <= paths


async def test_the_gate_refuses_non_admins_and_audits_admin_refusals(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    plain = await env.user("plain@example.com")
    rider = await make_actor(plain, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now()")
    row = new_ledger(superuser, plain, None, "committed")
    for response in (await rider.get(USAGE), await rider.post(refund_path(plain, row))):
        assert (response.status_code, reason(response)) == (403, "forbidden")
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
    admin = await env.user("a@example.com", role="admin")
    no_mfa = await make_actor(admin)
    assert reason(await no_mfa.get(USAGE)) == "mfa_required"
    no_stepup = await make_actor(admin, mfa=True)
    refused = await no_stepup.post(refund_path(plain, row))
    assert (refused.status_code, reason(refused)) == (403, "stepup_required")
    assert audit(superuser, admin) == [
        ("admin.request", "refused", "mfa_required"),
        ("admin.request", "refused", "stepup_required"),
    ]
    assert ledger_row(superuser, row).state == "committed"


def plant_two_users(conn: Connection, a: uuid.UUID, b: uuid.UUID) -> dict[str, uuid.UUID]:
    """A: a completed design (committed, 3000), a cancelled one (released, 500), a revision
    (no ledger row, 300), a refunded class and last month's run (9999). B: a running song design
    (reserved, instructor, 4000), a completed save (no runs) and a reservation whose job went
    with its class (``job_id`` NULL: not held)."""
    done = new_job(conn, a, "design_draft", "completed", credit_state="committed")
    new_ledger(conn, a, done, "committed")
    new_run(conn, a, done, "design_draft", 1000, tokens=100)
    new_run(conn, a, done, "design_draft", 2000, tokens=200)
    cancelled = new_job(conn, a, "design_draft", "cancelled", credit_state="released")
    new_ledger(conn, a, cancelled, "released", reason="cancelled")
    new_run(conn, a, cancelled, "design_draft", 500, tokens=50)
    revise = new_job(conn, a, "revise_draft", "completed")
    new_run(conn, a, revise, "revise_draft", 300, model="other-model", tokens=30)
    refunded = new_job(conn, a, "design_draft", "completed", credit_state="committed")
    new_ledger(conn, a, refunded, "refunded", reason="admin_refund")
    new_run(conn, a, done, "design_draft", 9999, last_month=True)
    running = new_job(conn, b, "design_with_songs", "running", credit_state="reserved")
    new_ledger(conn, b, running, "reserved", plan="instructor")
    new_run(conn, b, running, "design_with_songs", 4000, tokens=400)
    new_job(conn, b, "save_workout", "completed")
    new_ledger(conn, b, None, "reserved", plan="instructor")
    return {"done": done, "running": running}


async def test_the_totals_equal_the_planted_runs_and_ledger_rows_of_two_users(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    a = await env.user("ann@example.com")
    b = await env.user("bob@example.com")
    plant_two_users(superuser, a, b)
    response = await actor.get(USAGE)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    totals = body["totals"]
    assert (totals["cost_micros"], totals["runs"], totals["jobs"]) == (7800, 5, 4)
    assert (totals["input_tokens"], totals["output_tokens"]) == (780, 780)
    # Held: A's committed class and B's live reservation; the released, the refunded and the
    # job-less reservation are not classes, while the released job's 500 is in the cost.
    assert (totals["classes"], totals["revisions"], totals["saves"]) == (2, 1, 1)
    plans = {p["plan_id"]: p for p in body["by_plan"]}
    assert set(plans) == {"free", "instructor", None}
    free, instructor, rest = plans["free"], plans["instructor"], plans[None]
    assert (free["classes"], free["committed"], free["released"], free["refunded"]) == (1, 1, 1, 1)
    assert (free["sums"]["cost_micros"], free["sums"]["jobs"]) == (3500, 2)
    assert (instructor["classes"], instructor["reserved"], instructor["sums"]["cost_micros"]) == (
        1, 1, 4000
    )  # fmt: skip
    assert (rest["classes"], rest["sums"]["cost_micros"], rest["sums"]["runs"]) == (0, 300, 1)
    assert sum(p["sums"]["cost_micros"] for p in body["by_plan"]) == totals["cost_micros"]
    kinds = {(k["job_kind"], k["model"]): k["sums"]["cost_micros"] for k in body["by_kind_model"]}
    assert kinds == {
        ("design_draft", "fake-model"): 3500,
        ("design_with_songs", "fake-model"): 4000,
        ("revise_draft", "other-model"): 300,
    }
    completed = {k["kind"]: k["completed"] for k in body["jobs_by_kind"]}
    assert completed == {"design_draft": 2, "revise_draft": 1, "save_workout": 1}
    top = [(u["email"], u["cost_micros"], u["runs"], u["classes"]) for u in body["top_users"]]
    assert top == [("bob@example.com", 4000, 1, 1), ("ann@example.com", 3800, 4, 1)]
    assert body["price_versions"] == [{"price_version": "2026-10-29", "runs": 5}]
    assert body["prices_version"] == PRICES_VERSION
    priced = {(p["provider"], p["model"]) for p in body["prices"]}
    assert ("anthropic", "claude-opus-5-5") in priced
    assert audit(superuser, actor.user_id) == [("usage.read", "ok", None)]
    month = body["month"]
    params = superuser.execute(
        text("SELECT params FROM admin_audit WHERE action = 'usage.read'")
    ).scalar_one()
    assert params == {"month": month}


async def test_another_month_shows_only_its_own_runs(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    a = await env.user("ann@example.com")
    b = await env.user("bob@example.com")
    plant_two_users(superuser, a, b)
    last = superuser.execute(
        text("SELECT to_char(date_trunc('month', now() AT TIME ZONE 'UTC') - interval '1 day', "
             "'YYYY-MM')")
    ).scalar_one()  # fmt: skip
    body = (await actor.get(f"{USAGE}?month={last}")).json()
    assert (body["totals"]["cost_micros"], body["totals"]["classes"]) == (9999, 0)
    # The run's job holds a ledger row of this month's period: its cost shows under that plan.
    assert [(p["plan_id"], p["classes"], p["sums"]["cost_micros"]) for p in body["by_plan"]] == [
        ("free", 0, 9999)
    ]


@pytest.mark.parametrize("month", ["2026-13", "26-10", "2026-1", "x"])
async def test_a_bad_month_is_422_and_reads_nothing(
    env: Env, make_actor: Any, superuser: Connection, month: str
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.get(f"{USAGE}?month={month}")
    assert response.status_code == 422, response.text
    assert audit(superuser, actor.user_id) == []


async def test_a_refund_frees_a_slot_at_once_and_only_once(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    rider = await make_actor(target)
    rows = [
        new_ledger(
            superuser, target,
            new_job(superuser, target, "design_draft", "completed", credit_state="committed"),
            "committed",
        )
        for _ in range(3)
    ]  # fmt: skip
    assert (await rider.get("/api/account/plan")).json()["classes_held"] == 3
    before = ledger_row(superuser, rows[0])
    done = await actor.post(refund_path(target, rows[0]))
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["state"], body["reason"], body["refunded_by"], body["classes_held"]) == (
        "refunded", "admin_refund", str(actor.user_id), 2
    )  # fmt: skip
    assert (await rider.get("/api/account/plan")).json()["classes_held"] == 2
    after = ledger_row(superuser, rows[0])
    assert after.settled_at == before.settled_at  # the commit's time is kept
    again = await actor.post(refund_path(target, rows[0]))
    assert (again.status_code, reason(again)) == (409, "already_refunded")
    assert ledger_row(superuser, rows[0]) == after
    assert (await rider.get("/api/account/plan")).json()["classes_held"] == 2
    assert audit(superuser, actor.user_id) == [
        ("refund.credit", "ok", None),
        ("refund.credit", "refused", "already_refunded"),
    ]
    target_row = superuser.execute(
        text("SELECT target_user_id, target_kind, target_id FROM admin_audit "
             "WHERE action = 'refund.credit' AND outcome = 'ok'")
    ).one()  # fmt: skip
    assert tuple(target_row) == (target, "credit_ledger", str(rows[0]))


@pytest.mark.parametrize(
    ("state", "credit_state", "ledger_reason"),
    [("running", "reserved", None), ("cancelled", "released", "cancelled")],
)
async def test_a_reserved_or_released_row_is_409_and_unchanged(
    env: Env,
    make_actor: Any,
    superuser: Connection,
    state: str,
    credit_state: str,
    ledger_reason: str | None,
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    job = new_job(superuser, target, "design_draft", state, credit_state=credit_state)
    row = new_ledger(superuser, target, job, credit_state, reason=ledger_reason)
    before = ledger_row(superuser, row)
    response = await actor.post(refund_path(target, row))
    assert (response.status_code, reason(response)) == (409, "not_committed")
    assert ledger_row(superuser, row) == before
    assert audit(superuser, actor.user_id) == [("refund.credit", "refused", "not_committed")]


async def test_another_users_row_or_an_unknown_id_is_404(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    owner = await env.user("o@example.com")
    other = await env.user("x@example.com")
    row = new_ledger(superuser, owner, None, "committed")
    for path in (
        refund_path(other, row),  # the row exists, but not for this user
        refund_path(owner, uuid.uuid4()),
        refund_path(uuid.uuid4(), row),  # no such user
    ):
        response = await actor.post(path)
        assert (response.status_code, reason(response)) == (404, "not_found"), path
        assert response.headers["cache-control"] == "no-store"
    assert ledger_row(superuser, row).state == "committed"
    assert [r[1:] for r in audit(superuser, actor.user_id)] == [("refused", "not_found")] * 3


@pytest.mark.parametrize(
    ("state", "change"),
    [
        ("reserved", "state = 'refunded', reason = 'admin_refund', refunded_by = :by"),
        ("reserved", "state = 'released', reason = 'cancelled', settled_at = now()"),
        ("committed", "state = 'released', reason = 'admin_refund', refunded_by = :by"),
        ("released", "state = 'refunded', reason = 'admin_refund', refunded_by = :by"),
        ("refunded", "state = 'committed', reason = NULL, refunded_by = NULL"),
    ],
)
def test_the_ledger_trigger_refuses_maint_anything_but_a_committed_refund(
    superuser: Connection, maint: Connection, state: str, change: str
) -> None:
    """The route's ``WHERE state = 'committed'`` is not the only line: the trigger refuses maint
    a live reservation (and every other change) whatever SQL it sends."""
    owner = superuser.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at) "
            "VALUES (:e, :e, now()) RETURNING id"
        ),
        {"e": f"r-{secrets.token_hex(4)}@example.com"},
    ).scalar_one()
    job = new_job(superuser, owner, "design_draft", "running", credit_state="reserved")
    row = new_ledger(superuser, owner, job, state)
    before = ledger_row(superuser, row)
    transaction = maint.begin()
    maint.execute(text("SET TRANSACTION READ WRITE"))
    with pytest.raises(DBAPIError, match="credit_ledger: change refused"):
        maint.execute(
            text(f"UPDATE credit_ledger SET {change} WHERE id = :i"),  # noqa: S608
            {"i": row, "by": owner},
        )
    transaction.rollback()
    assert ledger_row(superuser, row) == before


async def test_no_usage_or_refund_response_contains_a_planted_secret(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    target = await env.user("t@example.com")
    plant = {
        "input": "PLANTED input " + secrets.token_hex(6),
        "password": "$argon2id$v=19$PLANTEDhash" + secrets.token_hex(4),
    }
    superuser.execute(
        text("UPDATE users SET password_hash = :p WHERE id = :o"),
        {"p": plant["password"], "o": target},
    )
    job = new_job(superuser, target, "design_draft", "completed", credit_state="committed",
                  job_input=plant["input"])  # fmt: skip
    row = new_ledger(superuser, target, job, "committed")
    new_run(superuser, target, job, "design_draft", 1234)
    key, request_hash = superuser.execute(
        text("SELECT idempotency_key, request_hash FROM jobs WHERE id = :j"), {"j": job}
    ).one()
    needles = [*plant.values(), key, bytes(request_hash).hex(), actor.token]
    responses = [
        await actor.get(USAGE),
        await actor.post(refund_path(target, row)),
        await actor.post(refund_path(target, row)),
    ]
    for response in responses:
        assert response.status_code < 500
        assert not [n for n in needles if n in response.text], response.request.url
