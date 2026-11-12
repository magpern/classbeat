"""`POST /api/billing/paddle/webhook` end to end over the fake Paddle (ADR 0032 sections 3 and 5,
#973): every answer code, replay, duplicate, reorder, a failed payment, a second purchase by the
same buyer, a cancel of a different subscription, classbeat's own change against an older Paddle
update, the Dummy replacement, the semaphore under a burst and the logs. No network: the fake
client lives in the app and its deliveries are posted to the app in process."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat.admin.db import webhook_session
from classbeat.billing import links_sql, webhook
from classbeat.billing.fake_paddle import Delivery, FakePaddleClient
from classbeat.billing.paddle_signature import HEADER, sign
from classbeat.billing.webhook_gate import WebhookLimiter
from test_admin_users import stepped_admin
from test_billing_routes import buy, change, count, make_tester, sub

SECRET = "pdl_ntfset_test_secret"
BILLING = "/api/account/billing"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        billing_provider="fake_paddle",
        paddle_webhook_secret=SECRET,
    ) as env:
        yield env


def paddle(env: Env) -> FakePaddleClient:
    return env.app.state.paddle_client


def poster(env: Env) -> Callable[[bytes, str], Any]:
    async def post(body: bytes, header: str) -> int:
        response = await env.client.post(webhook.PATH, content=body, headers={HEADER: header})
        return response.status_code

    return post


async def post(env: Env, delivery: Delivery, **kwargs: Any) -> Any:
    """`delivery` signed now (or at `ts`), optionally edited first (then re-signed)."""
    body = delivery.body
    if (edit := kwargs.get("edit")) is not None:
        doc = json.loads(body)
        edit(doc)
        body = json.dumps(doc).encode()
    ts = kwargs.get("ts", int(time.time()))
    header = kwargs.get("header", sign(body, SECRET, ts=ts))
    return await env.client.post(webhook.PATH, content=body, headers={HEADER: header})


def link(conn: Connection, user: Any) -> list[Any]:
    return list(conn.execute(
        text("SELECT checkout_ref, customer_ref, subscription_ref, activated_at, changed_at "
             "FROM billing_links WHERE owner_id = :o ORDER BY created_at, id"),
        {"o": user},
    ))  # fmt: skip


def refusals(conn: Connection) -> list[tuple[str, str]]:
    rows = conn.execute(text("SELECT event_id, reason FROM billing_webhook_refusals"))
    return [(r.event_id, r.reason) for r in rows]


def provider_of(conn: Connection, user: Any) -> str:
    return conn.execute(
        text("SELECT provider FROM subscriptions WHERE owner_id = :o"), {"o": user}
    ).scalar_one()


async def checkout(env: Env, actor: Any, plan: str = "instructor", amount: int = 1000) -> str:
    response = await buy(actor, plan, amount)
    assert response.status_code == 200, response.text
    url = response.json()["checkout_url"]
    assert url.startswith("https://classbeat.test/fake-paddle/checkout/txn_")
    return url.rsplit("/", 1)[1]


async def bought(env: Env, actor: Any) -> str:
    """A tester's checkout, paid and delivered: the subscription id."""
    sub_ref = paddle(env).pay(await checkout(env, actor))
    assert await paddle(env).deliver(poster(env)) == [200, 200]
    return sub_ref


@pytest.fixture
async def tester(env: Env, make_actor: Any, superuser: Connection, priced: None) -> Any:
    return await make_tester(env, make_actor, superuser)


# --- the happy path, replay and duplicate -----------------------------------------------------


async def test_a_checkout_links_first_and_the_signed_payment_starts_the_plan(
    env: Env, tester: Any, superuser: Connection
) -> None:
    ref = await checkout(env, tester)
    assert sub(superuser, tester.user_id) is None  # nothing until Paddle confirms
    ((checkout_ref, customer, sub_ref, activated, _),) = link(superuser, tester.user_id)
    assert (checkout_ref, customer, sub_ref, activated) == (ref, None, None, None)
    subscription = paddle(env).pay(ref)
    created, paid = paddle(env).outbox
    first = await post(env, created)
    assert (first.status_code, first.json()) == (200, {"outcome": "ignored",
                                                       "reason": "event_type_ignored"})  # fmt: skip
    assert (await post(env, paid)).json() == {"outcome": "applied", "reason": None}
    state = sub(superuser, tester.user_id)
    assert (state.plan_id, state.status) == ("instructor", "active")
    assert provider_of(superuser, tester.user_id) == "fake_paddle"  # never stored as paddle
    ((_, customer, sub_ref, activated, _),) = link(superuser, tester.user_id)
    assert sub_ref == subscription
    assert customer.startswith("ctm_")
    assert activated is not None
    rows = superuser.execute(
        text("SELECT action, outcome FROM billing_log WHERE owner_id = :o AND action = 'webhook'"),
        {"o": tester.user_id},
    ).all()
    assert [tuple(r) for r in rows] == [("webhook", "ok")]


async def test_a_replay_or_duplicate_changes_nothing_beyond_the_first(
    env: Env, tester: Any, superuser: Connection
) -> None:
    paddle(env).pay(await checkout(env, tester))
    _, paid = paddle(env).outbox
    assert (await post(env, paid)).json()["outcome"] == "applied"
    before = (count(superuser, "billing_events"), count(superuser, "entitlements"),
              count(superuser, "billing_log"))  # fmt: skip
    for _ in range(3):  # Paddle's retry, a duplicate delivery, a replay inside the window
        again = await post(env, paid)
        assert (again.status_code, again.json()["outcome"]) == (200, "duplicate")
    after = (count(superuser, "billing_events"), count(superuser, "entitlements"),
             count(superuser, "billing_log"))  # fmt: skip
    assert after == before  # a replay is found in the read-only lookup: not even a log row


# --- 401: before parsing and before any database access ---------------------------------------


async def test_a_bad_signature_is_401_and_touches_no_database(
    env: Env, tester: Any, superuser: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    paddle(env).pay(await checkout(env, tester))
    _, paid = paddle(env).outbox

    def no_database(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the database was reached before the signature")

    monkeypatch.setattr(webhook, "webhook_lookup", no_database)
    monkeypatch.setattr(webhook, "record_webhook_refusal", no_database)
    monkeypatch.setattr(webhook, "parse", no_database)
    log_rows = count(superuser, "billing_log")
    now = int(time.time())
    cases = {
        "signature_missing": {"header": ""},
        "signature_malformed": {"header": "ts=1;h1=zz"},
        "signature_mismatch": {"header": sign(paid.body, "another-secret", ts=now)},
        "signature_outside_window": {"ts": now - 301},
    }
    for expected, kwargs in cases.items():
        response = await post(env, paid, **kwargs)
        assert (response.status_code, response.json()["reason"]) == (401, expected)
    future = await post(env, paid, ts=now + 301)
    assert future.status_code == 401
    tampered = await post(env, paid, header=sign(paid.body, SECRET, ts=now),
                          edit=lambda d: d["data"].update(origin="api"))  # fmt: skip
    assert (tampered.status_code, tampered.json()["reason"]) == (401, "signature_mismatch")
    assert count(superuser, "billing_log") == log_rows
    assert sub(superuser, tester.user_id) is None


# --- 422: refused for good, counted once per event id -------------------------------------------


async def test_no_link_is_422_and_counted_once_across_retries(
    env: Env, tester: Any, superuser: Connection
) -> None:
    other = FakePaddleClient(secret=SECRET, base_url="https://elsewhere.test")
    from classbeat.billing.paddle_catalog import prices_of
    from classbeat.billing.paddle_client import CheckoutRequest
    from classbeat.billing.subscription_policy import Offer

    price = prices_of({"instructor": Offer("instructor", 1, 1000, "USD")})["instructor"]
    hosted = await other.create_checkout(CheckoutRequest(price, tester.user_id, "x" * 32))
    other.pay(hosted.checkout_ref)  # a purchase classbeat never linked
    _, paid = other.outbox
    for _ in range(3):
        response = await post(env, paid)
        assert (response.status_code, reason_of(response)) == (422, "no_link")
    assert refusals(superuser) == [(paid.event_id, "no_link")]
    assert sub(superuser, tester.user_id) is None


def reason_of(response: Any) -> str:
    return response.json()["reason"]


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (
            lambda d: d["data"]["items"][0]["price"]["unit_price"].update(amount="900"),
            "price_mismatch",
        ),
        (lambda d: d["data"]["details"]["totals"].update(subtotal="900"), "amount_mismatch"),
        (
            lambda d: d["data"]["items"][0]["price"]["billing_cycle"].update(interval="year"),
            "period_mismatch",
        ),
        (
            lambda d: d["data"]["custom_data"].update(classbeat_owner_id=str(uuid.uuid4())),
            "link_mismatch",
        ),
        (lambda d: d["data"].update(subscription_id="sub_notours"), "link_conflict"),
    ],
)
async def test_a_payment_that_disagrees_with_the_policy_or_the_link_is_422_with_no_write(
    env: Env, tester: Any, superuser: Connection, edit: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:  # fmt: skip
    paddle(env).pay(await checkout(env, tester))
    _, paid = paddle(env).outbox
    if expected == "link_conflict":
        # Another user's link already holds that subscription id.
        other = await env.user("other@example.com")
        superuser.execute(text(
            "INSERT INTO billing_links (owner_id, provider, checkout_ref, subscription_ref, "
            "activated_at) VALUES (:o, 'fake_paddle', 'txn_other', 'sub_notours', now())"
        ), {"o": other})  # fmt: skip
    for _ in range(2):
        response = await post(env, paid, edit=edit)
        assert (response.status_code, reason_of(response)) == (422, expected)
    assert refusals(superuser) == [(paid.event_id, expected)]
    assert sub(superuser, tester.user_id) is None
    assert count(superuser, "billing_events") == 0
    reasons = (
        superuser.execute(
            text("SELECT reason FROM billing_log WHERE action = 'webhook' AND outcome = 'refused'")
        )
        .scalars()
        .all()
    )
    assert set(reasons) == {expected}


async def test_a_signed_malformed_body_is_422_and_an_ignored_type_200(env: Env) -> None:
    now = int(time.time())
    garbage = b"not json"
    response = await env.client.post(
        webhook.PATH, content=garbage, headers={HEADER: sign(garbage, SECRET, ts=now)}
    )
    assert (response.status_code, reason_of(response)) == (422, "malformed")
    ignored = json.dumps({"event_type": "adjustment.created", "event_id": "evt_1",
                          "occurred_at": "2026-01-01T00:00:00Z", "data": {}}).encode()  # fmt: skip
    response = await env.client.post(
        webhook.PATH, content=ignored, headers={HEADER: sign(ignored, SECRET, ts=now)}
    )
    assert (response.status_code, reason_of(response)) == (200, "refund_policy_pending")


async def test_a_second_customer_claim_is_refused(
    env: Env, tester: Any, superuser: Connection
) -> None:
    """A customer id maps to at most one user: another user already holds it."""
    paddle(env).pay(await checkout(env, tester))
    _, paid = paddle(env).outbox
    customer = json.loads(paid.body)["data"]["customer_id"]
    other = await env.user("other@example.com")
    superuser.execute(text(
        "INSERT INTO billing_customers (owner_id, provider, customer_ref) "
        "VALUES (:o, 'fake_paddle', :c)"
    ), {"o": other, "c": customer})  # fmt: skip
    response = await post(env, paid)
    assert (response.status_code, reason_of(response)) == (422, "customer_conflict")
    assert sub(superuser, tester.user_id) is None
    held = superuser.execute(text("SELECT owner_id FROM billing_customers")).scalars().all()
    assert held == [other]


async def test_two_first_payments_racing_for_one_customer_leave_one_owner(
    env: Env, tester: Any, superuser: Connection, maint: Any
) -> None:
    """Two maint sessions claim the same customer for two owners at once: the unique key makes
    the second wait for the first and lose, whatever the timing (no check-then-insert gap)."""
    from classbeat.billing import links_sql

    other = await env.user("other@example.com")
    async with maint() as first, maint() as second:
        await first.execute(text("SET TRANSACTION READ WRITE"))
        await second.execute(text("SET TRANSACTION READ WRITE"))
        assert await links_sql.claim_customer(first, "fake_paddle", "ctm_race", tester.user_id)
        racing = asyncio.create_task(
            links_sql.claim_customer(second, "fake_paddle", "ctm_race", other)
        )
        await asyncio.sleep(0.5)
        assert not racing.done()  # blocked on the first transaction's row
        await first.commit()
        assert await asyncio.wait_for(racing, 10) is False
        await second.rollback()
    held = superuser.execute(text("SELECT owner_id FROM billing_customers")).scalars().all()
    assert held == [tester.user_id]


async def test_a_subscription_update_before_the_first_payment_is_409_then_lands(
    env: Env, tester: Any, superuser: Connection
) -> None:
    """Paddle may send `subscription.updated` before `transaction.completed`: the buyer's checkout
    is still waiting, so it is 409 (retried), not 422; it lands once the payment has."""
    sub_ref = paddle(env).pay(await checkout(env, tester))
    early = paddle(env)._subscription(paddle(env).subscriptions[sub_ref])  # pyright: ignore[reportPrivateUsage]
    response = await post(env, early)
    assert (response.status_code, reason_of(response)) == (409, "payment_pending")
    assert refusals(superuser) == []  # not counted: a retry can land
    assert await paddle(env).deliver(poster(env), hold={early.event_id}) == [200, 200]
    landed = await post(env, early)
    assert (landed.status_code, landed.json()["outcome"]) == (200, "ignored")


async def test_a_checkout_older_than_a_day_no_longer_waits_so_the_notice_is_a_counted_422(
    env: Env, tester: Any, superuser: Connection
) -> None:
    """An abandoned checkout must not make an unlinked subscription notice 409 forever."""
    sub_ref = paddle(env).pay(await checkout(env, tester))
    early = paddle(env)._subscription(paddle(env).subscriptions[sub_ref])  # pyright: ignore[reportPrivateUsage]

    def age(hours: int) -> None:
        superuser.execute(
            text("UPDATE billing_links SET created_at = now() - make_interval(hours => :h) "
                 "WHERE owner_id = :o"),
            {"h": hours, "o": tester.user_id},
        )  # fmt: skip

    age(links_sql.PENDING_HOURS - 1)  # still inside the day: Paddle retries
    response = await post(env, early)
    assert (response.status_code, reason_of(response)) == (409, "payment_pending")
    assert refusals(superuser) == []
    age(links_sql.PENDING_HOURS + 1)  # abandoned: the notice has no link and is counted once
    for _ in range(2):
        response = await post(env, early)
        assert (response.status_code, reason_of(response)) == (422, "no_link")
    assert refusals(superuser) == [(early.event_id, "no_link")]


async def test_a_late_payment_still_lands_on_a_checkout_older_than_a_day(
    env: Env, tester: Any, superuser: Connection
) -> None:
    """Expiry only ends the 409 wait: the paid transaction is found by its own checkout id."""
    ref = await checkout(env, tester)
    superuser.execute(
        text("UPDATE billing_links SET created_at = now() - interval '3 days' WHERE owner_id = :o"),
        {"o": tester.user_id},
    )
    paddle(env).pay(ref)
    assert await paddle(env).deliver(poster(env)) == [200, 200]
    assert sub(superuser, tester.user_id).status == "active"


async def test_a_subscription_update_with_no_checkout_at_all_is_422(
    env: Env, tester: Any, superuser: Connection
) -> None:
    sub_ref = await bought(env, tester)  # everything linked and activated: nothing is pending
    stray = paddle(env)._subscription(paddle(env).subscriptions[sub_ref])  # pyright: ignore[reportPrivateUsage]
    response = await post(env, stray, edit=lambda d: d["data"].update(id="sub_unknown"))
    assert (response.status_code, reason_of(response)) == (422, "no_link")
    assert refusals(superuser) == [(stray.event_id, "no_link")]


# --- 409: out of order, retried until the gap is filled -----------------------------------------


async def test_a_reordered_renewal_is_409_until_the_earlier_one_lands_then_once(
    env: Env, tester: Any, superuser: Connection
) -> None:
    sub_ref = await bought(env, tester)
    first, second = paddle(env).renew(sub_ref), paddle(env).renew(sub_ref)
    assert await paddle(env).deliver(poster(env), order=[1, 0]) == [409, 200]
    assert [d.event_id for d in paddle(env).outbox] == [second.event_id]
    assert await paddle(env).deliver(poster(env)) == [200]
    assert (await post(env, second)).json()["outcome"] == "duplicate"
    assert (await post(env, first)).json()["outcome"] == "duplicate"
    assert count(superuser, "entitlements", tester.user_id) == 4  # the grant, three periods
    assert refusals(superuser) == []  # a 409 is not counted: a retry can land


async def test_a_delayed_cancel_lands_after_the_renewal_it_was_held_behind(
    env: Env, tester: Any, superuser: Connection
) -> None:
    sub_ref = await bought(env, tester)
    cancel = await change(tester, "free")
    assert cancel.status_code == 200, cancel.text
    update = paddle(env).outbox[-1]
    assert await paddle(env).deliver(poster(env), hold={update.event_id}) == []
    ended = paddle(env).renew(sub_ref)
    assert await paddle(env).deliver(poster(env), hold={update.event_id}) == [200]
    assert sub(superuser, tester.user_id).status == "ended"
    assert (await post(env, update)).json()["reason"] == "no_active_subscription"
    assert ended.event_type == "subscription.canceled"


# --- a failed payment, a second purchase, another subscription -----------------------------------


async def test_a_failed_payment_leaves_the_plan_unchanged(
    env: Env, tester: Any, superuser: Connection
) -> None:
    ref = await checkout(env, tester)
    paddle(env).fail(ref)
    assert await paddle(env).deliver(poster(env)) == [200]
    assert sub(superuser, tester.user_id) is None
    billing = await tester.get(BILLING)
    assert billing.json()["subscription"] is None


async def test_a_second_purchase_by_the_same_buyer_and_the_old_subscriptions_cancel(
    env: Env, tester: Any, superuser: Connection
) -> None:
    old = await bought(env, tester)
    assert (await change(tester, "free")).status_code == 200
    paddle(env).renew(old)  # the period ends: Paddle cancels the old subscription
    await paddle(env).deliver(poster(env))
    assert sub(superuser, tester.user_id).status == "ended"
    new = await bought(env, tester)  # the same customer buys again
    assert new != old
    links = link(superuser, tester.user_id)
    assert len(links) == 2
    assert links[0].customer_ref == links[1].customer_ref
    # A late notification about the old subscription must not touch the new one.
    paddle(env).subscriptions[old].status = "active"
    late_update = paddle(env)._subscription(paddle(env).subscriptions[old])  # pyright: ignore[reportPrivateUsage]
    paddle(env).subscriptions[old].cancel = True
    late_cancel = paddle(env).renew(old)
    for delivery in (late_update, late_cancel):
        response = await post(env, delivery)
        assert (response.status_code, reason_of(response)) == (200, "other_subscription")
    state = sub(superuser, tester.user_id)
    assert (state.status, state.cancel_at_end) == ("active", False)


async def test_classbeats_cancel_wins_over_an_older_paddle_update(
    env: Env, tester: Any, superuser: Connection
) -> None:
    sub_ref = await bought(env, tester)
    before = paddle(env)._subscription(paddle(env).subscriptions[sub_ref])  # pyright: ignore[reportPrivateUsage]
    assert (await change(tester, "free")).status_code == 200
    ((*_, changed_at),) = link(superuser, tester.user_id)
    assert changed_at is not None  # classbeat stored its own change time
    older = (changed_at - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    response = await post(env, before, edit=lambda d: d.update(occurred_at=older))
    assert (response.status_code, reason_of(response)) == (200, "stale_change")
    assert sub(superuser, tester.user_id).cancel_at_end
    # Paddle's own notice of the same cancel, newer: no change, and the time moves forward.
    own = paddle(env).outbox[-1]
    newer = (changed_at + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    response = await post(env, own, edit=lambda d: d.update(occurred_at=newer))
    assert reason_of(response) == "no_change"
    ((*_, moved),) = link(superuser, tester.user_id)
    assert moved > changed_at
    assert sub(superuser, tester.user_id).cancel_at_end


async def test_a_newer_paddle_resume_applies_and_stores_its_time(
    env: Env, tester: Any, superuser: Connection
) -> None:
    sub_ref = await bought(env, tester)
    assert (await change(tester, "free")).status_code == 200
    paddle(env).subscriptions[sub_ref].cancel = False  # resumed in Paddle's own UI
    resumed = paddle(env)._subscription(paddle(env).subscriptions[sub_ref])  # pyright: ignore[reportPrivateUsage]
    later = (datetime.now(UTC) + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    response = await post(env, resumed, edit=lambda d: d.update(occurred_at=later))
    assert reason_of(response) is None
    assert not sub(superuser, tester.user_id).cancel_at_end
    ((*_, changed_at),) = link(superuser, tester.user_id)
    assert changed_at.isoformat().startswith(later[:19])


async def test_a_links_change_time_only_moves_forward(
    env: Env, tester: Any, superuser: Connection
) -> None:
    await bought(env, tester)
    first = datetime(2031, 5, 10, 12, tzinfo=UTC)
    stamps = [first, first - timedelta(days=1), first + timedelta(days=1), first]
    seen: list[datetime] = []
    async with webhook_session(env.app.state.admin_db, tester.user_id, "r" * 32) as session:
        row = await links_sql.current(session, tester.user_id, "fake_paddle")
        assert row is not None
        now = datetime.now(UTC)
        for at in stamps:
            await links_sql.changed(session, row, at, now)  # `row` is stale, the SQL decides
            seen.append(
                (await session.execute(
                    text("SELECT changed_at FROM billing_links WHERE id = :i"), {"i": row.id}
                )).scalar_one()
            )  # fmt: skip
    assert seen == [first, first, first + timedelta(days=1), first + timedelta(days=1)]


# --- upgrade and downgrade through Paddle ---------------------------------------------------------


async def test_an_upgrade_is_charged_by_paddle_and_a_downgrade_renews_lower(
    env: Env, tester: Any, superuser: Connection
) -> None:
    sub_ref = await bought(env, tester)
    offers = {o["plan"]: o for o in (await tester.get(BILLING)).json()["offers"]}
    assert offers["pro"]["action"] == "upgrade"
    up = await buy(tester, "pro", offers["pro"]["charge_minor"])
    assert up.status_code == 200, up.text
    assert up.json()["checkout_url"] is None
    assert sub(superuser, tester.user_id).plan_id == "instructor"  # until Paddle confirms
    await paddle(env).deliver(poster(env))
    assert sub(superuser, tester.user_id).plan_id == "pro"
    assert (await change(tester, "instructor")).status_code == 200
    assert sub(superuser, tester.user_id).next_plan_id == "instructor"
    paddle(env).renew(sub_ref)
    await paddle(env).deliver(poster(env))
    state = sub(superuser, tester.user_id)
    assert (state.plan_id, state.next_plan_id) == ("instructor", None)


# --- the Dummy replacement ------------------------------------------------------------------------


async def test_a_paddle_start_replaces_a_dummy_subscription_and_revokes_its_rows(
    env: Env, tester: Any, superuser: Connection
) -> None:
    now = datetime.now(UTC) - timedelta(days=2)
    ends = now + timedelta(days=30)
    event = superuser.execute(text(
        "INSERT INTO billing_events (owner_id, provider, event_id, kind, plan_id, period_start, "
        "period_end, amount_minor, currency, outcome) VALUES (:o, 'dummy', 'dummy_x', 'started', "
        "'pro', :s, :e, 2500, 'USD', 'applied') RETURNING id"
    ), {"o": tester.user_id, "s": now, "e": ends}).scalar_one()  # fmt: skip
    superuser.execute(text(
        "INSERT INTO entitlements (owner_id, plan_id, features, source, starts_at, ends_at, "
        "billing_event_id) VALUES (:o, 'pro', '{}', 'subscription', :s, :e, :ev)"
    ), {"o": tester.user_id, "s": now, "e": ends, "ev": event})  # fmt: skip
    superuser.execute(text(
        "INSERT INTO subscriptions (owner_id, provider, plan_id, anchor_day, period_start, "
        "period_end, basis_minor, basis_currency) VALUES (:o, 'dummy', 'pro', :a, :s, :e, 2500, "
        "'USD')"
    ), {"o": tester.user_id, "a": now.day, "s": now, "e": ends})  # fmt: skip
    offers = {o["plan"]: o for o in (await tester.get(BILLING)).json()["offers"]}
    assert offers["instructor"]["action"] == "buy"  # a Paddle checkout replaces Dummy
    await bought(env, tester)
    assert provider_of(superuser, tester.user_id) == "fake_paddle"
    assert sub(superuser, tester.user_id).plan_id == "instructor"
    revoked = superuser.execute(text(
        "SELECT revoked_at IS NOT NULL FROM entitlements WHERE billing_event_id = :ev"
    ), {"ev": event}).scalar_one()  # fmt: skip
    assert revoked


# --- 503: the semaphore of 1, under a burst; 429 and 413 ------------------------------------------


async def test_a_burst_runs_one_at_a_time_and_the_rest_get_503(
    env: Env, tester: Any, superuser: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    paddle(env).pay(await checkout(env, tester))
    _, paid = paddle(env).outbox
    entered, release = asyncio.Event(), asyncio.Event()
    inside = most = 0
    real = webhook.links_sql.find

    async def slow(*args: Any) -> Any:
        nonlocal inside, most
        inside += 1
        most = max(most, inside)
        entered.set()
        await release.wait()
        inside -= 1
        return await real(*args)

    monkeypatch.setattr(webhook.links_sql, "find", slow)
    first = asyncio.create_task(post(env, paid))
    await entered.wait()
    rest = await asyncio.gather(*(post(env, paid) for _ in range(8)))
    assert {(r.status_code, reason_of(r)) for r in rest} == {(503, "busy")}
    release.set()
    assert (await first).json()["outcome"] == "applied"
    assert most == 1
    assert (await post(env, paid)).json()["outcome"] == "duplicate"  # free again


async def test_the_rate_limit_and_the_body_cap_answer_before_the_signature(env: Env) -> None:
    big = b"x" * (40 * 1024)
    response = await env.client.post(webhook.PATH, content=big, headers={HEADER: "ts=1;h1=0"})
    assert (response.status_code, reason_of(response)) == (413, "body_too_large")
    env.app.state.paddle_webhook.limiter = WebhookLimiter(per_ip=2)
    statuses = [(await env.client.post(webhook.PATH, content=b"{}")).status_code for _ in range(3)]
    assert statuses == [401, 401, 429]


# --- nothing secret in the logs -------------------------------------------------------------------


async def test_no_body_header_secret_or_id_is_logged(
    env: Env, tester: Any, superuser: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    sub_ref = await bought(env, tester)
    paddle(env).renew(sub_ref)
    delivery = paddle(env).outbox[0]
    header = paddle(env).sign(delivery)
    await env.client.post(webhook.PATH, content=delivery.body, headers={HEADER: header})
    await post(env, delivery, edit=lambda d: d["data"]["details"]["totals"].update(subtotal="1"))
    await post(env, delivery, header="ts=1;h1=" + "0" * 64)
    logged = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    customer = json.loads(delivery.body)["data"]["customer_id"]
    for secret in (SECRET, header, delivery.event_id, sub_ref, customer, "subtotal"):
        assert secret not in logged
    assert "paddle webhook" in caplog.text


async def test_a_cookie_on_the_webhook_is_never_looked_up(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No session lookup (a database read) before the signature: the general limit skips it."""
    from classbeat.auth import middleware

    async def no_lookup(*_args: Any) -> Any:
        raise AssertionError("a session was looked up")

    monkeypatch.setattr(middleware, "resolve_request", no_lookup)
    response = await env.client.post(
        webhook.PATH, content=b"{}", headers={"Cookie": "__Host-classbeat_session=x"}
    )
    assert (response.status_code, reason_of(response)) == (401, "signature_missing")


async def test_admins_see_the_refusals_counted_once_per_event(
    env: Env, tester: Any, make_actor: Any, superuser: Connection
) -> None:
    paddle(env).pay(await checkout(env, tester))
    _, paid = paddle(env).outbox

    def cheaper(doc: dict[str, Any]) -> None:
        doc["data"]["details"]["totals"]["subtotal"] = "900"

    for _ in range(3):
        assert (await post(env, paid, edit=cheaper)).status_code == 422
    admin = await stepped_admin(env, make_actor)
    body = (await admin.get("/api/admin/billing")).json()
    assert body["webhook_refusals"] == [
        {"provider": "fake_paddle", "reason": "amount_mismatch", "events": 1}
    ]
