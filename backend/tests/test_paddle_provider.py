"""`PaddleProvider` over `FakePaddleClient` (ADR 0032 section 2, #973): no database, no network.

The fake's notifications are read back through the pure layer the webhook uses (signature,
`parse`, `map_notification`), so what it sends is what the route accepts.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from classbeat.billing.apply_policy import State
from classbeat.billing.choices import current_of
from classbeat.billing.fake_paddle import CHECKOUT_PATH, Delivery, FakePaddleClient
from classbeat.billing.paddle_catalog import catalog_of, price_ref, prices_of
from classbeat.billing.paddle_events import map_notification, parse, refs
from classbeat.billing.paddle_links import gate
from classbeat.billing.paddle_provider import PaddleProvider, change_event_id
from classbeat.billing.paddle_signature import Verified, verify
from classbeat.billing.paddle_types import Link, Mapped, Notification, Refs, Reject, Skip, Stored
from classbeat.billing.provider import ChangeScheduled, Started, Upgraded
from classbeat.billing.subscription_policy import (
    Offer,
    Quote,
    quote_buy,
    quote_cancel,
    quote_upgrade,
)

SECRET = "pdl_ntfset_test_secret"
OWNER = UUID("7d3c1f0e-5b9a-4c2d-8e6f-1a2b3c4d5e6f")
START = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
OFFERS = {
    "free": Offer("free", 0, None, None),
    "instructor": Offer("instructor", 1, 1000, "USD"),
    "pro": Offer("pro", 2, 2500, "USD"),
}
PRICES = prices_of(OFFERS)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def fake(clock: Clock | None = None) -> FakePaddleClient:
    return FakePaddleClient(
        secret=SECRET, base_url="https://classbeat.test", clock=clock or Clock(START)
    )


def provider(client: FakePaddleClient, sub: str | None = None) -> PaddleProvider:
    return PaddleProvider(
        client, provider="fake_paddle", now=START, prices=PRICES, subscription_ref=sub
    )


def buy(offer: Offer = OFFERS["instructor"]) -> Quote:
    quote = quote_buy(offer, START)
    assert isinstance(quote, Quote)
    return quote


def doc(d: Delivery) -> dict[str, Any]:
    return json.loads(d.body)


def mapped(d: Delivery, stored: State | None, link: Link) -> Mapped | Skip | Reject:
    n = parse(d.body)
    assert isinstance(n, Notification)
    held = None if stored is None else Stored(stored)
    return map_notification(
        n, owner_id=OWNER, provider="fake_paddle", catalog=catalog_of(OFFERS), stored=held,
        link=link,
    )  # fmt: skip


async def bought(client: FakePaddleClient, key: str = "k" * 16) -> tuple[str, str, Started]:
    """A checkout paid: (checkout ref, subscription ref, the Started it maps to)."""
    made = await provider(client).checkout(buy(), owner_id=OWNER, idempotency_key=key)
    assert made.event is None
    assert made.checkout_ref is not None
    sub = client.pay(made.checkout_ref)
    created, paid = client.outbox
    assert created.event_type == "subscription.created"
    link = Link("fake_paddle", made.checkout_ref, None, None)
    out = mapped(paid, None, link)
    assert isinstance(out, Mapped)
    assert isinstance(out.event, Started)
    assert out.subscription_ref == sub
    return made.checkout_ref, sub, out.event


async def test_a_buy_is_a_hosted_checkout_with_no_event_and_a_retry_gets_the_same_one() -> None:
    client = fake()
    quote = buy()
    first = await provider(client).checkout(quote, owner_id=OWNER, idempotency_key="key-123456")
    again = await provider(client).checkout(quote, owner_id=OWNER, idempotency_key="key-123456")
    other = await provider(client).checkout(quote, owner_id=OWNER, idempotency_key="key-654321")
    assert first.event is None
    assert first == again
    assert other.checkout_ref != first.checkout_ref
    assert first.url == f"https://classbeat.test{CHECKOUT_PATH}/{first.checkout_ref}"
    assert client.outbox == []  # nothing is charged or notified until the buyer pays


async def test_a_paid_checkout_maps_to_started_at_the_policy_amount() -> None:
    _, _, started = await bought(fake())
    assert (started.plan_id, started.amount_minor, started.currency) == ("instructor", 1000, "USD")
    assert started.period_start == START
    assert started.anchor_day == 10


async def test_a_quote_that_disagrees_with_the_paddle_price_is_refused() -> None:
    client = fake()
    quote = buy(Offer("instructor", 1, 900, "USD"))
    with pytest.raises(ValueError, match="disagrees"):
        await provider(client).checkout(quote, owner_id=OWNER, idempotency_key="key-123456")


async def test_an_upgrade_is_charged_by_paddle_at_the_writers_amount() -> None:
    clock = Clock(START)
    client = fake(clock)
    _, sub, started = await bought(client)
    client.outbox.clear()
    state = State("fake_paddle", "instructor", started.period_start, started.period_end, 10,
                  1000, "USD")  # fmt: skip
    clock.at = START + timedelta(days=9)
    quote = quote_upgrade(current_of(state, 1), OFFERS["pro"], clock.at)
    assert isinstance(quote, Quote)
    paddle = PaddleProvider(client, provider="fake_paddle", now=clock.at, prices=PRICES,
                            subscription_ref=sub)  # fmt: skip
    made = await paddle.checkout(quote, owner_id=OWNER, idempotency_key="up-12345")
    assert made.event is None
    assert made.url is None
    charge = next(d for d in client.outbox if d.event_type == "transaction.completed")
    out = mapped(charge, state, Link("fake_paddle", "txn_x", None, sub))
    assert isinstance(out, Mapped)
    assert isinstance(out.event, Upgraded)
    assert out.event.amount_minor == quote.amount_minor
    assert out.event.period_start == clock.at
    assert out.event.period_end == state.period_end


async def test_a_cancel_tells_paddle_and_is_recorded_by_classbeat_at_once() -> None:
    client = fake()
    _, sub, started = await bought(client)
    client.outbox.clear()
    state = State("fake_paddle", "instructor", started.period_start, started.period_end, 10,
                  1000, "USD")  # fmt: skip
    event = await provider(client, sub).schedule_change(
        quote_cancel(current_of(state, 1)), owner_id=OWNER, idempotency_key="cancel-1234"
    )
    assert isinstance(event, ChangeScheduled)
    assert event.cancel_at_end
    assert event.occurred_at == START  # the request's time: the link's last change time
    assert event.event_id == change_event_id(OWNER, "cancel-1234")
    assert "cancel-1234" not in event.event_id
    (update,) = client.outbox
    assert doc(update)["data"]["scheduled_change"]["action"] == "cancel"
    renewal = client.renew(sub)
    assert renewal.event_type == "subscription.canceled"


async def test_a_change_without_a_subscription_is_refused() -> None:
    state = State("fake_paddle", "instructor", START, START + timedelta(days=31), 10, 1000, "USD")
    with pytest.raises(ValueError, match="no Paddle subscription"):
        await provider(fake()).schedule_change(
            quote_cancel(current_of(state, 1)), owner_id=OWNER, idempotency_key="cancel-1234"
        )


async def test_deliveries_are_signed_with_the_test_secret_now() -> None:
    client = fake()
    await bought(client)
    for delivery in client.outbox:
        header = client.sign(delivery)
        assert isinstance(
            verify(header, delivery.body, [SECRET], now=int(datetime.now(UTC).timestamp())),
            Verified,
        )
        assert SECRET not in header
        assert SECRET.encode() not in delivery.body


async def test_deliver_retries_what_was_not_2xx_and_can_hold_and_reorder() -> None:
    client = fake()
    _, sub, _ = await bought(client)
    third = client.renew(sub)
    sent: list[str] = []
    answers = {"subscription.created": 200, "transaction.completed": 409}

    async def post(body: bytes, header: str) -> int:
        kind = json.loads(body)["event_type"]
        sent.append(kind)
        return answers[kind]

    assert await client.deliver(post, order=[2, 1, 0], hold={third.event_id}) == [409, 200]
    assert [d.event_type for d in client.outbox] == ["transaction.completed"] * 2
    answers["transaction.completed"] = 200
    assert await client.deliver(post) == [200, 200]
    assert client.outbox == []
    assert await client.send(third, post) == 200  # a duplicate: sent again on purpose


async def test_a_failed_payment_starts_nothing() -> None:
    client = fake()
    quote = buy()
    made = await provider(client).checkout(quote, owner_id=OWNER, idempotency_key="key-123456")
    assert made.checkout_ref is not None
    failed = client.fail(made.checkout_ref)
    assert parse(failed.body) == Skip("payment_failed")
    assert client.subscriptions == {}


async def test_a_second_purchase_by_the_same_buyer_keeps_the_customer() -> None:
    client = fake()
    _, first, _ = await bought(client)
    client.outbox.clear()
    _, second, _ = await bought(client, "another-key-123")
    assert first != second
    assert client.subscriptions[first].customer_ref == client.subscriptions[second].customer_ref


def test_the_catalog_names_each_offered_plan_by_a_paddle_shaped_id() -> None:
    assert set(PRICES) == {"instructor", "pro"}
    assert PRICES["pro"].ref == price_ref("pro")
    assert catalog_of(OFFERS).plans[price_ref("instructor")] == "instructor"
    raw = {"event_type": "transaction.completed", "event_id": "evt_1",
           "occurred_at": "2026-03-10T12:00:00Z",
           "data": {"id": "txn_a", "subscription_id": "sub_a"}}  # fmt: skip
    n = parse(json.dumps(raw).encode())
    assert isinstance(n, Notification)
    assert refs(n) == Refs("txn_a", "sub_a", None, None)


@pytest.mark.parametrize(
    ("event_type", "starts", "link_sub", "current", "expected"),
    [
        ("transaction.completed", True, None, None, None),
        ("transaction.completed", False, "sub_a", "sub_a", None),
        ("transaction.completed", False, "sub_old", "sub_a", Reject("other_subscription")),
        ("subscription.updated", False, "sub_a", "sub_a", None),
        ("subscription.canceled", False, "sub_old", "sub_a", Skip("other_subscription")),
        ("subscription.updated", False, "sub_a", None, Skip("other_subscription")),
    ],
)
def test_only_the_current_subscription_changes_the_stored_state(
    event_type: str, starts: bool, link_sub: str | None, current: str | None, expected: object
) -> None:
    assert (
        gate(event_type=event_type, starts=starts, link_subscription=link_sub, current=current)
        == expected
    )
