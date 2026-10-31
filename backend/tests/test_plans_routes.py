"""``GET /api/account/plan`` and ``POST /api/account/trial`` (ADR 0011 sections 2, 4 and 9; PL1,
#212), and the terms every app-written entitlement keeps (``classbeat.plans.entitle``): a code or
trial entitlement always ends, a trial ranks at most Instructor and its length is clamped to
``CLASSBEAT_TRIAL_DAYS_MAX`` when it starts. Fakes only."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.db import user_session
from classbeat.plans.account import cost_share
from classbeat.plans.entitle import TermsRefused, add_entitlement
from ctx import make_context
from jobapi import Browser
from plantools import about, grant, plant_code, set_trial

PLAN = "/api/account/plan"
TRIAL = "/api/account/trial"


def trials(conn: Connection, owner: UUID) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT plan_id, ends_at - starts_at AS span, ends_at FROM entitlements "
                "WHERE owner_id = :o AND source = 'trial'"
            ),
            {"o": owner},
        ).all()
    )


def plant_run(conn: Connection, owner: UUID, cost: int, started: str = "now()") -> None:
    conn.execute(
        text(
            "INSERT INTO agent_runs (owner_id, job_kind, price_version, stage, attempt, provider, "  # noqa: S608
            "model, effort, prompt_version, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, cost_micros, outcome, started_at, finished_at) VALUES (:o, "
            "'design_draft', 'v', 'design', 1, 'fake', 'fake-model', 'medium', 'x', 1, 1, 0, 0, "
            f":c, 'submitted', {started}, now())"
        ),
        {"o": owner, "c": cost},
    )


def plant_ledger(conn: Connection, owner: UUID, state: str, period: str = "this") -> None:
    month = "date_trunc('month', now() AT TIME ZONE 'UTC')"
    if period == "last":
        month = f"({month} - interval '1 month')"
    conn.execute(
        text(
            "INSERT INTO credit_ledger (owner_id, period, plan_id, state) "  # noqa: S608
            f"VALUES (:o, CAST({month} AS date), 'free', :s)"
        ),
        {"o": owner, "s": state},
    )


# --- GET /api/account/plan --------------------------------------------------------------------


async def test_the_plan_needs_a_session(env: Any) -> None:
    response = await env.client.get(PLAN)
    assert (response.status_code, response.json()) == (
        401,
        {"detail": {"reason": "not_authenticated"}},
    )


async def test_a_new_account_is_on_free_with_its_limits(admin: Connection, browsers: Any) -> None:
    alice: Browser = await browsers(mowl=False)
    body = (await alice.get(PLAN)).json()
    now = datetime.now(UTC)
    first_next = datetime(now.year + now.month // 12, now.month % 12 + 1, 1, tzinfo=UTC)
    assert datetime.fromisoformat(body.pop("period_end")) == first_next
    assert body == {
        "plan": "free",
        "name": "Free",
        "source": None,
        "ends_at": None,
        "limits": {"classes_per_month": 3, "revisions_per_class": 3, "concurrent_jobs": 1},
        "classes_held": 0,
        "cost_share": 0.0,
        "features": [],
        "trial": None,
    }


async def test_a_grant_shows_with_its_end_and_falls_back_to_free_after_it(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    bob: Browser = await browsers(mowl=False)
    ends = admin.execute(text("SELECT now() + interval '3 days'")).scalar_one()
    grant(admin, alice.user_id, "pro", ends_at=ends, features=["song_picking"])
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["name"], body["source"]) == ("pro", "Pro", "grant")
    assert datetime.fromisoformat(body["ends_at"]) == ends
    # Pro allows 3 jobs and 25 revisions; the environment caps them (2 and 25 by default).
    assert body["limits"] == {
        "classes_per_month": 100,
        "revisions_per_class": 25,
        "concurrent_jobs": 2,
    }
    assert body["features"] == ["song_picking"]
    assert (await bob.get(PLAN)).json()["plan"] == "free"  # owner-scoped
    admin.execute(
        text(
            "UPDATE entitlements SET starts_at = now() - interval '4 days', "
            "ends_at = now() - interval '1 second' WHERE owner_id = :o"
        ),
        {"o": alice.user_id},
    )
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["source"], body["ends_at"], body["features"]) == (
        "free",
        None,
        None,
        [],
    )


async def test_a_grant_without_end_outlasts_a_code_of_the_same_plan(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    grant(admin, alice.user_id, "instructor")
    code, _ = plant_code(admin, plan="instructor", days=30)
    assert (await alice.post("/api/account/redeem", {"code": code})).status_code == 200
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["source"], body["ends_at"]) == ("instructor", "grant", None)


async def test_classes_held_and_the_cost_share_count_this_period_only(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    bob: Browser = await browsers(mowl=False)
    plant_ledger(admin, alice.user_id, "committed")
    plant_ledger(admin, alice.user_id, "committed")
    plant_ledger(admin, alice.user_id, "reserved")  # no job (its class was deleted): not held
    plant_ledger(admin, alice.user_id, "committed", period="last")
    plant_ledger(admin, bob.user_id, "committed")
    plant_run(admin, alice.user_id, 2_500_000)
    plant_run(admin, alice.user_id, 1_999_999)
    plant_run(admin, alice.user_id, 9_000_000, "date_trunc('month', now()) - interval '1 day'")
    plant_run(admin, bob.user_id, 9_000_000)
    body = (await alice.get(PLAN)).json()
    assert (body["classes_held"], body["cost_share"]) == (
        2,
        0.44,
    )  # 4.5 of Free's $10, rounded down
    assert "cost_micros" not in str(body)
    assert "monthly_cost" not in str(body)


def test_the_cost_share_is_rounded_down_and_capped() -> None:
    assert cost_share(0, 10_000_000) == 0.0
    assert cost_share(9_999_999, 10_000_000) == 0.99
    assert cost_share(10_000_000, 10_000_000) == 1.0
    assert cost_share(30_000_000, 10_000_000) == 1.0
    assert (cost_share(0, 0), cost_share(1, 0)) == (0.0, 1.0)


# --- POST /api/account/trial ------------------------------------------------------------------


async def test_trial_is_off_without_a_setting_or_with_zero_days(
    admin: Connection, browsers: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    off = {"detail": {"reason": "trial_off"}}
    response = await alice.post(TRIAL)
    assert (response.status_code, response.json()) == (409, off)
    for value in (
        {"plan": "instructor", "days": 0},
        {"plan": "instructor"},
        {"plan": "nope", "days": 5},
        [1],
        {"plan": "instructor", "days": True},
    ):
        set_trial(admin, value)
        response = await alice.post(TRIAL)
        assert (response.status_code, response.json()) == (409, off), value
        assert (await alice.get(PLAN)).json()["trial"] is None
    assert trials(admin, alice.user_id) == []


async def test_a_trial_starts_once_and_shows_on_the_plan(admin: Connection, browsers: Any) -> None:
    alice: Browser = await browsers(mowl=False)
    set_trial(admin, {"plan": "instructor", "days": 14})
    assert (await alice.get(PLAN)).json()["trial"] == {"plan": "instructor", "days": 14}
    started = await alice.post(TRIAL)
    assert started.status_code == 200, started.text
    assert started.json()["plan"] == "instructor"
    assert about(started.json()["ends_at"], 14)
    body = (await alice.get(PLAN)).json()
    assert (body["plan"], body["source"], body["trial"]) == ("instructor", "trial", None)
    again = await alice.post(TRIAL)
    assert (again.status_code, again.json()) == (409, {"detail": {"reason": "trial_used"}})
    # Ever: a revoked or ended trial still used it up.
    admin.execute(
        text("UPDATE entitlements SET revoked_at = now() WHERE owner_id = :o"), {"o": alice.user_id}
    )
    assert (await alice.post(TRIAL)).status_code == 409
    assert (await alice.get(PLAN)).json()["trial"] is None
    [row] = trials(admin, alice.user_id)
    assert (row.plan_id, row.span.days) == ("instructor", 14)


async def test_parallel_trials_give_one(admin: Connection, browsers: Any) -> None:
    alice: Browser = await browsers(mowl=False)
    set_trial(admin, {"plan": "instructor", "days": 14})
    headers = await alice.headers()
    responses = await asyncio.gather(*(alice.client.post(TRIAL, headers=headers) for _ in range(6)))
    assert sorted(r.status_code for r in responses) == [200] + [409] * 5
    assert {r.json()["detail"]["reason"] for r in responses if r.status_code == 409} == {
        "trial_used"
    }
    assert len(trials(admin, alice.user_id)) == 1


async def test_a_stored_trial_above_the_bounds_is_clamped_when_used(
    admin: Connection, browsers: Any, env: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    set_trial(admin, {"plan": "pro", "days": 200})
    # The plan to Instructor, the days to CLASSBEAT_TRIAL_DAYS_MAX (30 by default).
    assert (await alice.get(PLAN)).json()["trial"] == {"plan": "instructor", "days": 30}
    # A bound lowered later applies to a setting stored before it.
    env.app.state.settings = env.settings.model_copy(update={"trial_days_max": 7})
    assert (await alice.get(PLAN)).json()["trial"] == {"plan": "instructor", "days": 7}
    started = await alice.post(TRIAL)
    assert started.status_code == 200
    assert started.json()["plan"] == "instructor"
    assert about(started.json()["ends_at"], 7)
    [row] = trials(admin, alice.user_id)
    assert (row.plan_id, row.span.days, row.ends_at is not None) == ("instructor", 7, True)
    env.app.state.settings = env.settings.model_copy(update={"trial_days_max": 0})
    bob: Browser = await browsers(mowl=False)
    assert (await bob.post(TRIAL)).json() == {"detail": {"reason": "trial_off"}}


async def test_trial_needs_csrf(admin: Connection, browsers: Any) -> None:
    alice: Browser = await browsers(mowl=False)
    set_trial(admin, {"plan": "instructor", "days": 14})
    headers = await alice.headers()
    headers["X-CSRF-Token"] = "0" * 64
    refused = await alice.client.post(TRIAL, headers=headers)
    alice.client.cookies.clear()
    assert (refused.status_code, refused.json()) == (
        403,
        {"detail": {"reason": "csrf_token_invalid"}},
    )
    assert trials(admin, alice.user_id) == []


# --- the terms of add_entitlement -------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "plan", "features", "days", "code"),
    [
        ("trial", "pro", (), 14, False),  # above Instructor
        ("trial", "nope", (), 14, False),  # unknown plan
        ("trial", None, ("song_picking",), 14, False),  # a trial is a plan
        ("trial", "instructor", ("song_picking",), 14, False),
        ("trial", "instructor", (), 14, True),
        ("trial", "instructor", (), 0, False),  # no end
        ("trial", "instructor", (), 367, False),
        ("code", "instructor", (), 30, False),  # a code entitlement names its code
        ("code", None, (), 30, True),  # grants nothing
        ("grant", "instructor", (), 30, False),  # the admin's, never the app's
    ],
)
async def test_add_entitlement_refuses_anything_outside_the_terms(
    admin: Connection,
    browsers: Any,
    env: Any,
    source: Any,
    plan: str | None,
    features: tuple[str, ...],
    days: int,
    code: bool,
) -> None:
    alice: Browser = await browsers(mowl=False)
    code_id = plant_code(admin)[1] if code else None
    ctx = make_context(alice.user_id)
    with pytest.raises(TermsRefused):
        async with user_session(env.factory, ctx) as db:
            await add_entitlement(
                db, ctx, source=source, plan_id=plan, features=features, days=days, code_id=code_id
            )
    assert admin.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0


async def test_add_entitlement_refuses_a_days_value_that_is_not_an_int(
    admin: Connection, browsers: Any, env: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    ctx = make_context(alice.user_id)
    for days in (14.5, True, None):
        with pytest.raises(TermsRefused):
            async with user_session(env.factory, ctx) as db:
                await add_entitlement(db, ctx, source="trial", plan_id="instructor", days=days)  # pyright: ignore[reportArgumentType]


async def test_a_trial_at_instructor_or_below_is_allowed_and_always_ends(
    admin: Connection, browsers: Any, env: Any
) -> None:
    alice: Browser = await browsers(mowl=False)
    ctx = make_context(alice.user_id)
    async with user_session(env.factory, ctx) as db:
        added = await add_entitlement(db, ctx, source="trial", plan_id="free", days=1)
    assert added is not None
    assert about(added.ends_at, 1)
    async with user_session(env.factory, ctx) as db:  # the second is no trial at all
        assert await add_entitlement(db, ctx, source="trial", plan_id="instructor", days=5) is None
    [row] = trials(admin, alice.user_id)
    assert row.plan_id == "free"
    assert row.ends_at is not None
