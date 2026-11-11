"""The billing routes, the subscription writer and the fast-forward end to end (#948, ADR 0031):
who may buy, buy, upgrade, downgrade, cancel, resume, the log rows, the admin's fast-forward, the
export, account deletion and isolation. The Dummy provider only; no network, no real account."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from test_admin_users import stepped_admin

BILLING = "/api/account/billing"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        yield env


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


async def make_tester(
    env: Env, make_actor: Any, conn: Connection, email: str = "t@example.com"
) -> Any:
    """A signed-in user holding an active `billing_test` grant (ADR 0031 section 6)."""
    user_id = await env.user(email)
    conn.execute(
        text(
            "INSERT INTO entitlements (owner_id, features, source, ends_at) "
            "VALUES (:o, '{billing_test}', 'grant', now() + interval '30 days')"
        ),
        {"o": user_id},
    )
    actor = await make_actor(user_id)
    actor.user_id = user_id
    return actor


async def buy(actor: Any, plan: str, amount: int, key: str | None = None) -> Any:
    return await actor.call(
        "POST", f"{BILLING}/checkout", {"plan": plan, "expected_amount_minor": amount},
        {"Idempotency-Key": key or uuid.uuid4().hex},
    )  # fmt: skip


async def change(actor: Any, plan: str, key: str | None = None) -> Any:
    return await actor.call(
        "POST", f"{BILLING}/change", {"plan": plan}, {"Idempotency-Key": key or uuid.uuid4().hex}
    )


def sub(conn: Connection, user_id: Any) -> Any:
    return conn.execute(
        text(
            "SELECT plan_id, status, period_start, period_end, anchor_day, basis_minor, "
            "next_plan_id, cancel_at_end FROM subscriptions WHERE owner_id = :o"
        ),
        {"o": user_id},
    ).one_or_none()


def rows(conn: Connection, sql: str, **params: Any) -> list[Any]:
    return list(conn.execute(text(sql), params))


def count(conn: Connection, table: str, owner: Any = None) -> int:
    where = " WHERE owner_id = :o" if owner else ""
    sql = f"SELECT count(*) FROM {table}{where}"  # noqa: S608
    return conn.execute(text(sql), {"o": owner} if owner else {}).scalar_one()


# --- who may buy ----------------------------------------------------------------------------


async def test_a_user_who_may_not_buy_gets_403_on_every_route_and_sees_no_price(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    plain = await make_actor(await env.user("plain@example.com"))
    responses = [
        await plain.get(BILLING),
        await buy(plain, "instructor", 1000),
        await change(plain, "free"),
    ]
    for response in responses:
        assert (response.status_code, reason(response)) == (403, "billing_closed")
        assert "1000" not in response.text
        assert "price" not in response.text
    assert count(superuser, "billing_log") == 0
    assert count(superuser, "billing_events") == 0
    assert count(superuser, "admin_audit") == 0


async def test_an_expired_or_revoked_tester_grant_does_not_open_billing(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    superuser.execute(text("UPDATE entitlements SET revoked_at = now()"))
    assert (await actor.get(BILLING)).status_code == 403


async def test_the_write_transaction_checks_who_may_buy_again(
    env: Env,
    make_actor: Any,
    superuser: Connection,
    priced: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate said yes on the app role; the grant is gone by the time the maint transaction
    runs, so the in-transaction recheck (`_recheck`) refuses and nothing is written."""
    from classbeat.billing import routes

    actor = await make_tester(env, make_actor, superuser)
    real = routes.may_buy
    calls = 0

    async def flips(*args: Any, **kwargs: Any) -> bool:
        nonlocal calls
        calls += 1
        return True if calls == 1 else await real(*args, **kwargs)

    monkeypatch.setattr(routes, "may_buy", flips)
    superuser.execute(text("UPDATE entitlements SET revoked_at = now()"))
    response = await buy(actor, "instructor", 1000)
    assert (response.status_code, reason(response)) == (403, "billing_closed")
    assert calls == 2
    assert count(superuser, "billing_events") == 0


async def test_a_tester_and_an_admin_can_buy_while_billing_is_off(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    assert env.settings.billing_enabled is False
    t = await make_tester(env, make_actor, superuser)
    admin = await stepped_admin(env, make_actor)
    for actor in (t, admin):
        got = await actor.get(BILLING)
        assert got.status_code == 200
        assert got.json()["can_buy"] is True
        assert (await buy(actor, "instructor", 1000)).status_code == 200
        assert sub(superuser, actor.user_id).plan_id == "instructor"


def test_billing_enabled_true_is_refused_at_settings_validation() -> None:
    from classbeat.config import Settings

    with pytest.raises(ValueError, match="billing_enabled"):
        Settings.model_validate({"billing_enabled": True})


# --- buy, upgrade, downgrade, cancel, resume ------------------------------------------------


async def test_a_buy_stores_the_subscription_its_event_and_one_entitlement_and_one_log_row(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    offers = {o["plan"]: o for o in (await actor.get(BILLING)).json()["offers"]}
    assert (offers["instructor"]["action"], offers["instructor"]["charge_minor"]) == ("buy", 1000)
    response = await buy(actor, "instructor", 1000)
    assert response.status_code == 200, response.text
    state = sub(superuser, actor.user_id)
    assert (state.plan_id, state.status, state.basis_minor, state.cancel_at_end) == (
        "instructor", "active", 1000, False,
    )  # fmt: skip
    (event,) = rows(
        superuser, "SELECT kind, outcome, amount_minor, currency, event_id FROM billing_events"
    )
    assert (event.kind, event.outcome, event.amount_minor, event.currency) == (
        "started", "applied", 1000, "USD",
    )  # fmt: skip
    (grant,) = rows(
        superuser,
        "SELECT plan_id, features, ends_at, billing_event_id FROM entitlements "
        "WHERE source = 'subscription'",
    )
    assert (grant.plan_id, grant.features, grant.ends_at) == ("instructor", [], state.period_end)
    assert grant.billing_event_id is not None
    # One billing_log row for the checkout (the GET above wrote its own), no admin_audit row.
    actions = [r.action for r in rows(superuser, "SELECT action FROM billing_log ORDER BY id")]
    assert sorted(actions) == ["checkout", "prices"]
    assert count(superuser, "admin_audit") == 0
    plan = (await actor.get("/api/account/plan")).json()
    assert plan["plan"] == "instructor"


async def test_a_retried_click_is_a_duplicate_and_the_event_id_holds_nothing_of_the_key(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    key = "zebra-marmalade-lantern-ordinary"
    assert (await buy(actor, "instructor", 1000, key)).status_code == 200
    assert (await buy(actor, "instructor", 1000, key)).status_code == 200
    assert count(superuser, "billing_events") == 1
    assert count(superuser, "entitlements", actor.user_id) == 2  # the tester grant and one row
    (event_id,) = [r.event_id for r in rows(superuser, "SELECT event_id FROM billing_events")]
    assert "marmalade" not in event_id  # a distinctive part of the key
    assert key not in event_id
    assert event_id.startswith("dummy_")


async def test_a_missing_or_bad_idempotency_key_is_refused(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    body = {"plan": "instructor", "expected_amount_minor": 1000}
    for extra in (None, {"Idempotency-Key": "short"}):
        response = await actor.call("POST", f"{BILLING}/checkout", body, extra)
        assert (response.status_code, reason(response)) == (400, "idempotency_key_invalid")
    assert count(superuser, "billing_events") == 0


async def test_a_price_that_moved_is_quote_changed_and_the_refusal_is_logged(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    response = await buy(actor, "instructor", 999)
    assert (response.status_code, reason(response)) == (409, "quote_changed")
    assert sub(superuser, actor.user_id) is None
    (log,) = rows(superuser, "SELECT action, outcome, reason FROM billing_log")
    assert tuple(log) == ("checkout", "refused", "quote_changed")
    assert (await buy(actor, "nosuch", 0)).status_code == 422
    assert (await buy(actor, "free", 0)).status_code == 422


async def test_an_upgrade_charges_the_quote_keeps_the_period_end_and_leaves_the_old_row(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    assert (await buy(actor, "instructor", 1000)).status_code == 200
    before = sub(superuser, actor.user_id)
    offers = {o["plan"]: o for o in (await actor.get(BILLING)).json()["offers"]}
    assert offers["pro"]["action"] == "upgrade"
    charge = offers["pro"]["charge_minor"]
    assert 0 <= charge <= 1500  # never above the difference, never negative
    assert (await buy(actor, "pro", charge + 1)).status_code == 409
    assert (await buy(actor, "pro", charge)).status_code == 200
    after = sub(superuser, actor.user_id)
    assert (after.plan_id, after.period_end, after.basis_minor) == ("pro", before.period_end, 2500)
    events = rows(superuser, "SELECT kind, amount_minor FROM billing_events ORDER BY created_at")
    assert [tuple(e) for e in events] == [("started", 1000), ("upgraded", charge)]
    grants = rows(
        superuser,
        "SELECT plan_id, revoked_at FROM entitlements WHERE source = 'subscription' "
        "ORDER BY plan_id",
    )
    assert [(g.plan_id, g.revoked_at) for g in grants] == [("instructor", None), ("pro", None)]
    assert (await actor.get("/api/account/plan")).json()["plan"] == "pro"
    assert all(e.amount_minor >= 0 for e in events)


async def test_a_downgrade_and_a_cancel_change_nothing_now_and_resume_clears_them(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    assert (await buy(actor, "pro", 2500)).status_code == 200
    grants = count(superuser, "entitlements", actor.user_id)

    assert (await change(actor, "instructor")).status_code == 200
    state = sub(superuser, actor.user_id)
    assert (state.plan_id, state.next_plan_id, state.cancel_at_end) == ("pro", "instructor", False)

    # Free is a cancel: it replaces the scheduled downgrade, never both together.
    assert (await change(actor, "free")).status_code == 200
    state = sub(superuser, actor.user_id)
    assert (state.plan_id, state.next_plan_id, state.cancel_at_end) == ("pro", None, True)

    assert (await change(actor, "pro")).status_code == 200  # the current plan resumes
    state = sub(superuser, actor.user_id)
    assert (state.next_plan_id, state.cancel_at_end) == (None, False)

    assert count(superuser, "entitlements", actor.user_id) == grants  # nothing granted or taken
    kinds = [r.kind for r in rows(superuser, "SELECT kind FROM billing_events ORDER BY created_at")]
    assert kinds == ["started", "change_scheduled", "change_scheduled", "change_scheduled"]
    assert (await change(actor, "nosuch")).status_code == 422
    paid = [r.amount_minor for r in rows(superuser, "SELECT amount_minor FROM billing_events")]
    assert paid[1:] == [0, 0, 0]


async def test_a_change_without_a_subscription_or_to_a_higher_plan_is_refused(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    actor = await make_tester(env, make_actor, superuser)
    assert reason(await change(actor, "free")) == "no_subscription"
    assert (await buy(actor, "instructor", 1000)).status_code == 200
    assert reason(await change(actor, "pro")) == "use_checkout"
    assert reason(await buy(actor, "instructor", 0)) == "already_on_plan"


# --- the admin's fast-forward ---------------------------------------------------------------


def fast_forward_path(user_id: Any) -> str:
    return f"/api/admin/users/{user_id}/billing/fast-forward"


async def test_a_fast_forward_needs_a_step_up_and_writes_one_audit_row(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    assert (await buy(t, "instructor", 1000)).status_code == 200
    admin_id = await env.user("a@example.com", role="admin")
    no_stepup = await make_actor(admin_id, mfa=True)
    refused = await no_stepup.call("POST", fast_forward_path(t.user_id))
    assert (refused.status_code, reason(refused)) == (403, "stepup_required")
    stepped = await stepped_admin(env, make_actor, "b@example.com")
    ok = await stepped.call("POST", fast_forward_path(t.user_id))
    assert ok.status_code == 200, ok.text
    assert ok.json()["outcome"] == "renewed"
    audit = rows(
        superuser,
        "SELECT action, outcome FROM admin_audit WHERE actor_id = :a",
        a=stepped.user_id,
    )
    assert [tuple(r) for r in audit] == [("billing.fast_forward", "ok")]
    again = await stepped.call("POST", fast_forward_path(t.user_id))
    assert again.status_code == 200  # a new period each time: a new event id


async def test_a_fast_forward_revokes_every_running_row_and_starts_the_next_period(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    assert (await buy(t, "instructor", 1000)).status_code == 200
    charge = (await t.get(BILLING)).json()["offers"][1]["charge_minor"]
    assert (await buy(t, "pro", charge)).status_code == 200  # an upgrade: two rows run
    before = sub(superuser, t.user_id)
    admin = await stepped_admin(env, make_actor)
    assert (await admin.call("POST", fast_forward_path(t.user_id))).status_code == 200
    after = sub(superuser, t.user_id)
    assert after.period_start > before.period_start
    assert after.period_end > after.period_start
    assert after.anchor_day == after.period_start.day  # the anchor resets to today's day
    old = rows(
        superuser,
        "SELECT revoked_at, revoked_by FROM entitlements WHERE source = 'subscription' "
        "AND ends_at = :e",
        e=before.period_end,
    )
    assert len(old) == 2
    assert all(r.revoked_at is not None and r.revoked_by for r in old)
    running = rows(
        superuser,
        "SELECT plan_id FROM entitlements WHERE source = 'subscription' AND revoked_at IS NULL",
    )
    assert [r.plan_id for r in running] == ["pro"]
    assert (await t.get("/api/account/plan")).json()["plan"] == "pro"
    assert count(superuser, "admin_audit") == 1
    assert count(superuser, "billing_log") == 3  # buy, prices, buy: the fast-forward adds none


async def test_a_fast_forward_applies_a_scheduled_downgrade_and_a_cancel_ends_it(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    admin = await stepped_admin(env, make_actor)
    assert (await buy(t, "pro", 2500)).status_code == 200
    assert (await change(t, "instructor")).status_code == 200
    assert (await admin.call("POST", fast_forward_path(t.user_id))).status_code == 200
    state = sub(superuser, t.user_id)
    assert (state.plan_id, state.next_plan_id, state.basis_minor) == ("instructor", None, 1000)
    assert (await t.get("/api/account/plan")).json()["plan"] == "instructor"

    assert (await change(t, "free")).status_code == 200
    done = await admin.call("POST", fast_forward_path(t.user_id))
    assert (done.status_code, done.json()["outcome"]) == (200, "ended")
    assert sub(superuser, t.user_id).status == "ended"
    assert (await t.get("/api/account/plan")).json()["plan"] == "free"
    nothing = await admin.call("POST", fast_forward_path(t.user_id))
    assert (nothing.status_code, reason(nothing)) == (409, "no_subscription")


async def test_a_fast_forward_for_a_user_without_a_subscription_is_refused_and_audited(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    admin = await stepped_admin(env, make_actor)
    refused = await admin.call("POST", fast_forward_path(t.user_id))
    assert (refused.status_code, reason(refused)) == (409, "no_subscription")
    audit = rows(superuser, "SELECT action, outcome, reason FROM admin_audit")
    assert [tuple(r) for r in audit] == [("billing.fast_forward", "refused", "no_subscription")]


async def test_a_non_admin_cannot_fast_forward_or_list(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    assert (await t.call("POST", fast_forward_path(t.user_id))).status_code == 403
    assert (await t.get("/api/admin/billing")).status_code == 403
    assert count(superuser, "admin_audit") == 0


async def test_the_admin_lists_dummy_subscriptions_under_their_own_heading(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    assert (await buy(t, "instructor", 1000)).status_code == 200
    admin = await stepped_admin(env, make_actor)
    body = (await admin.get("/api/admin/billing")).json()
    assert body["provider"] == "dummy"
    assert [(s["user_id"], s["plan"]) for s in body["subscriptions"]] == [
        (str(t.user_id), "instructor")
    ]
    assert "amount" not in json.dumps(body)


# --- export, deletion, isolation ------------------------------------------------------------


async def test_the_export_holds_the_subscription_and_no_price(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    key = "walrus-pancake-trombone-harbour"
    assert (await buy(t, "instructor", 1000, key)).status_code == 200
    response = await t.call("POST", "/api/account/export")
    assert response.status_code == 200, response.text
    document = response.json()
    assert [s["plan_id"] for s in document["subscriptions"]] == ["instructor"]
    assert [e["kind"] for e in document["billing_events"]] == ["started"]
    assert document["billing_log"][0]["action"] == "checkout"
    for section in ("subscriptions", "billing_events"):
        for row in document[section]:
            assert not {"amount_minor", "currency", "basis_minor", "basis_currency"} & set(row)
    assert key not in response.text
    assert any("amounts charged" in line for line in document["not_included"])


async def test_deleting_an_account_with_a_subscription_events_and_a_log_succeeds(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    t = await make_tester(env, make_actor, superuser)
    other = await make_tester(env, make_actor, superuser, "o@example.com")
    for actor in (t, other):
        assert (await buy(actor, "instructor", 1000)).status_code == 200
    assert (await change(t, "free")).status_code == 200
    await env.run("UPDATE sessions SET stepup_at = now()")
    gone = await t.call("POST", "/api/account/delete", {"confirm_email": "t@example.com"})
    assert gone.status_code == 204, gone.text
    for table in ("subscriptions", "billing_events", "billing_log", "entitlements"):
        assert count(superuser, table, t.user_id) == 0
    assert count(superuser, "subscriptions", other.user_id) == 1  # the other user is untouched
    assert count(superuser, "billing_events", other.user_id) == 1


async def test_two_users_never_see_or_change_each_others_billing(
    env: Env, make_actor: Any, superuser: Connection, priced: None
) -> None:
    a = await make_tester(env, make_actor, superuser, "a1@example.com")
    b = await make_tester(env, make_actor, superuser, "b1@example.com")
    assert (await buy(a, "pro", 2500, "same-key-for-both-users")).status_code == 200
    seen = (await b.get(BILLING)).json()
    assert seen["subscription"] is None
    assert (await change(b, "free")).status_code == 409  # B has nothing to cancel
    # The same Idempotency-Key under another owner is another event, not A's replay.
    assert (await buy(b, "instructor", 1000, "same-key-for-both-users")).status_code == 200
    assert sub(superuser, a.user_id).plan_id == "pro"
    assert sub(superuser, b.user_id).plan_id == "instructor"
    assert count(superuser, "billing_events") == 2
    ids = {r.event_id for r in rows(superuser, "SELECT event_id FROM billing_events")}
    assert len(ids) == 2
    export_b = (await b.call("POST", "/api/account/export")).json()
    assert [s["plan_id"] for s in export_b["subscriptions"]] == ["instructor"]
    log_b = {r["action"] for r in export_b["billing_log"]}
    assert log_b <= {"prices", "checkout", "change"}
    assert len(export_b["billing_log"]) == 3
