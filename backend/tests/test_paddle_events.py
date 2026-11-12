"""Paddle notifications to neutral events (ADR 0032 section 1, #972): table-driven, no I/O.

Fixtures (`tests/fixtures/paddle/`) follow Paddle's public example payloads; each case edits a
copy. The last tests run the mapped events through `apply_policy.decide`, the writer's rules.
"""

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from classbeat.billing.apply_policy import Applied, Expected, State, decide
from classbeat.billing.choices import current_of
from classbeat.billing.paddle_events import (
    Catalog,
    Mapped,
    Reject,
    Skip,
    map_notification,
    owner_of,
)
from classbeat.billing.provider import ChangeScheduled, Ended, Renewed, Started, Upgraded
from classbeat.billing.subscription_policy import Offer, Quote, quote_buy, quote_upgrade

FIXTURES = Path(__file__).parent / "fixtures" / "paddle"
OWNER = UUID("7d3c1f0e-5b9a-4c2d-8e6f-1a2b3c4d5e6f")
SUB = "sub_01hv8x2a3b4c5d6e7f8g9h0j1k"
IND, PRO = "pri_01hv8wzaaaaaaaaaaaaaaaaaaa", "pri_01hv8wzbbbbbbbbbbbbbbbbbbb"
OFFERS = {
    "free": Offer("free", 0, None, None),
    "instructor": Offer("instructor", 1, 1000, "USD"),
    "pro": Offer("pro", 2, 3000, "USD"),
}
CATALOG = Catalog({IND: "instructor", PRO: "pro"}, OFFERS)
JAN31 = datetime(2026, 1, 31, 10, tzinfo=UTC)
FEB28 = datetime(2026, 2, 28, 10, tzinfo=UTC)
MAR31 = datetime(2026, 3, 31, 10, tzinfo=UTC)
LATER = datetime(2026, 2, 28, 11, tzinfo=UTC)  # a newer subscription's period start
ACTIVE = State("paddle", "instructor", JAN31, FEB28, 31, 1000, "USD")
CANCELLING = State("paddle", "instructor", JAN31, FEB28, 31, 1000, "USD", cancel_at_end=True)

Doc = dict[str, Any]


def load(name: str) -> Doc:
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


def body(doc: Doc) -> bytes:
    return json.dumps(doc).encode()


def run(name: str, edit: Callable[[Doc], object] | None = None, state: State | None = None):
    doc = copy.deepcopy(load(name))
    if edit is not None:
        edit(doc)
    return map_notification(body(doc), CATALOG, state)


def item(doc: Doc) -> Doc:
    return doc["data"]["items"][0]


# --- what each notification maps to ---------------------------------------------------------


def test_first_payment_is_started() -> None:
    assert run("transaction_completed_web") == Mapped(
        OWNER,
        SUB,
        Started(
            provider="paddle", event_id="evt_01hv8xa1a1a1a1a1a1a1a1a1a1", period_start=JAN31,
            period_end=FEB28, plan_id="instructor", amount_minor=1000, currency="USD",
            anchor_day=31,
        ),
    )  # fmt: skip


def test_recurring_payment_is_renewed() -> None:
    assert run("transaction_completed_recurring", state=ACTIVE) == Mapped(
        OWNER,
        SUB,
        Renewed(
            provider="paddle", event_id="evt_01hv8xa2a2a2a2a2a2a2a2a2a2", period_start=FEB28,
            period_end=MAR31, plan_id="instructor", amount_minor=1000, currency="USD",
        ),
    )  # fmt: skip


def test_proration_charge_is_upgraded() -> None:
    assert run("transaction_completed_update", state=ACTIVE) == Mapped(
        OWNER,
        SUB,
        Upgraded(
            provider="paddle", event_id="evt_01hv8xa3a3a3a3a3a3a3a3a3a3",
            period_start=datetime(2026, 2, 8, 12, tzinfo=UTC), period_end=FEB28,
            plan_id="pro", amount_minor=1429, currency="USD",
        ),
    )  # fmt: skip


def test_scheduled_cancel_is_change_scheduled() -> None:
    assert run("subscription_updated_cancel", state=ACTIVE) == Mapped(
        OWNER,
        SUB,
        ChangeScheduled(
            provider="paddle", event_id="evt_01hv8xa4a4a4a4a4a4a4a4a4a4", period_start=JAN31,
            period_end=FEB28, cancel_at_end=True,
        ),
    )  # fmt: skip


def test_removed_cancel_is_a_resume() -> None:
    assert run("subscription_updated_resume", state=CANCELLING) == Mapped(
        OWNER,
        SUB,
        ChangeScheduled(
            provider="paddle", event_id="evt_01hv8xa5a5a5a5a5a5a5a5a5a5", period_start=JAN31,
            period_end=FEB28, cancel_at_end=False,
        ),
    )  # fmt: skip


def test_canceled_is_ended_for_the_stored_period() -> None:
    assert run("subscription_canceled", state=ACTIVE) == Mapped(
        OWNER,
        SUB,
        Ended(
            provider="paddle", event_id="evt_01hv8xa6a6a6a6a6a6a6a6a6a6", period_start=JAN31,
            period_end=FEB28,
        ),
    )  # fmt: skip


def test_event_id_is_paddles_notification_id_on_every_source() -> None:
    for name in (
        "transaction_completed_web",
        "transaction_completed_recurring",
        "subscription_updated_cancel",
        "subscription_canceled",
    ):
        mapped = run(name, state=ACTIVE)
        assert isinstance(mapped, Mapped), name
        assert mapped.event.event_id == load(name)["event_id"], name
        assert mapped.event.event_id.startswith("evt_")


def test_mapping_is_deterministic_for_a_retried_delivery() -> None:
    doc = load("transaction_completed_web")
    retried = copy.deepcopy(doc)
    retried["notification_id"] = "ntf_01hv8xzzzzzzzzzzzzzzzzzzzz"  # a new delivery attempt
    assert map_notification(body(retried), CATALOG, None) == map_notification(
        body(doc), CATALOG, None
    )


# --- acknowledged and ignored ---------------------------------------------------------------


def _set(path: list[str | int], value: object) -> Callable[[Doc], None]:
    def edit(doc: Doc) -> None:
        target: Any = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return edit


@pytest.mark.parametrize(
    ("name", "edit", "state", "reason"),
    [
        ("subscription_created", None, None, "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "subscription.activated"), None,
         "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "subscription.past_due"), ACTIVE,
         "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "transaction.paid"), None,
         "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "transaction.payment_failed"), ACTIVE,
         "payment_failed"),
        ("adjustment_created", None, ACTIVE, "refund_policy_pending"),
        ("adjustment_created", _set(["event_type"], "adjustment.updated"), ACTIVE,
         "refund_policy_pending"),
        ("transaction_completed_web", _set(["data", "subscription_id"], None), None,
         "not_a_subscription"),
        ("transaction_completed_web", _set(["data", "origin"], "subscription_charge"), ACTIVE,
         "origin_ignored"),
        ("transaction_completed_web",
         _set(["data", "origin"], "subscription_payment_method_change"), ACTIVE,
         "origin_ignored"),
        ("subscription_updated_cancel", None, CANCELLING, "no_change"),  # a retry of ours
        ("subscription_updated_resume", None, ACTIVE, "no_change"),  # e.g. a new card
        ("subscription_updated_cancel", _set(["data", "scheduled_change", "action"], "pause"),
         ACTIVE, "change_not_supported"),
        ("subscription_canceled", None, None, "no_active_subscription"),
        ("subscription_canceled", None,
         State("dummy", "instructor", JAN31, FEB28, 31, 0, "USD"), "no_active_subscription"),
        ("subscription_canceled", None,
         State("paddle", "instructor", JAN31, FEB28, 31, 1000, "USD", status="ended"),
         "no_active_subscription"),
        ("subscription_canceled", None,  # an older subscription's cancel arrives late
         State("paddle", "instructor", LATER, MAR31, 31, 1000, "USD"),
         "stale"),
    ],
)  # fmt: skip
def test_skipped(
    name: str, edit: Callable[[Doc], None] | None, state: State | None, reason: str
) -> None:
    assert run(name, edit, state) == Skip(reason)


def test_ignored_types_need_no_valid_data() -> None:
    doc = {"event_type": "subscription.created"}
    assert map_notification(body(doc), CATALOG, None) == Skip("event_type_ignored")


# --- refused: nothing is written ------------------------------------------------------------


def _drop(path: list[str | int]) -> Callable[[Doc], None]:
    def edit(doc: Doc) -> None:
        target: Any = doc
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return edit


def _two_items(doc: Doc) -> None:
    doc["data"]["items"].append(copy.deepcopy(item(doc)))


PRICE = ["data", "items", 0, "price"]
TOTALS = ["data", "details", "totals"]


@pytest.mark.parametrize(
    ("name", "edit", "reason"),
    [
        # disagreement with the policy
        ("transaction_completed_web", _set([*PRICE, "id"], "pri_01hv8wzunknownunknownunkno"),
         "unknown_price"),
        ("transaction_completed_web", _set([*PRICE, "unit_price", "amount"], "999"),
         "price_mismatch"),
        ("transaction_completed_web", _set([*PRICE, "unit_price", "currency_code"], "EUR"),
         "price_mismatch"),
        ("transaction_completed_web", _set(["data", "currency_code"], "EUR"), "price_mismatch"),
        ("transaction_completed_web", _set([*PRICE, "billing_cycle", "interval"], "year"),
         "period_mismatch"),
        ("transaction_completed_web", _set([*PRICE, "billing_cycle", "frequency"], 3),
         "period_mismatch"),
        ("transaction_completed_web", _set([*PRICE, "billing_cycle", "frequency"], True),
         "period_mismatch"),
        ("transaction_completed_web",
         _set(["data", "billing_period", "ends_at"], "2026-03-03T10:00:00Z"), "period_mismatch"),
        ("transaction_completed_web",
         _set(["data", "billing_period", "ends_at"], "2026-02-28T10:00:01Z"), "period_mismatch"),
        ("transaction_completed_web", _set([*TOTALS, "discount"], "100"),
         "discount_not_supported"),
        ("transaction_completed_web", _two_items, "items_not_single"),
        ("transaction_completed_web", _set(["data", "items", 0, "quantity"], 2),
         "items_not_single"),
        ("transaction_completed_web", _set(["data", "items", 0, "quantity"], True),
         "items_not_single"),
        ("transaction_completed_web", _set(["data", "items"], []), "items_not_single"),
        ("subscription_updated_cancel",
         _set(["data", "scheduled_change", "effective_at"], "2026-02-12T00:00:00Z"),
         "cancel_not_at_period_end"),
        # who it is for
        ("transaction_completed_web", _set(["data", "custom_data"], None), "owner_missing"),
        ("transaction_completed_web", _set(["data", "custom_data"], {}), "owner_missing"),
        ("transaction_completed_web",
         _set(["data", "custom_data", "classbeat_owner_id"], "not-a-uuid"), "owner_missing"),
        ("transaction_completed_web", _set(["data", "custom_data", "classbeat_owner_id"], 7),
         "owner_missing"),
        ("subscription_canceled", _set(["data", "custom_data"], None), "owner_missing"),
        # periods Paddle must send
        ("transaction_completed_web", _set(["data", "billing_period"], None), "period_missing"),
        ("transaction_completed_update", _drop(["data", "items", 0, "proration"]), "malformed"),
        ("transaction_completed_update",
         _set(["data", "items", 0, "proration", "billing_period"], None), "period_missing"),
        ("subscription_updated_cancel", _set(["data", "current_billing_period"], None),
         "period_missing"),
        # malformed fields
        ("transaction_completed_web", _drop(["event_id"]), "malformed"),
        ("transaction_completed_web", _set(["event_id"], "evt_01H-BAD"), "malformed"),
        ("transaction_completed_web", _set(["event_id"], "evt_" + "a" * 300), "malformed"),
        ("transaction_completed_web", _set(["event_type"], 7), "malformed"),
        ("transaction_completed_web", _set(["data"], []), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], "-1000"), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], 1000), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], "10.00"), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], "9" * 16), "malformed"),
        ("transaction_completed_web",
         _set(["data", "billing_period", "starts_at"], "2026-01-31T10:00:00"), "malformed"),
        ("transaction_completed_web",
         _set(["data", "billing_period", "starts_at"], "yesterday"), "malformed"),
        ("transaction_completed_web",
         _set(["data", "billing_period", "starts_at"], "2026-02-28T10:00:00Z"), "malformed"),
        ("transaction_completed_web", _set(["data", "currency_code"], "usd"), "malformed"),
        ("transaction_completed_web", _set(["data", "subscription_id"], "sub 1"), "malformed"),
        ("subscription_canceled", _set(["data", "canceled_at"], None), "malformed"),
    ],
)  # fmt: skip
def test_rejected(name: str, edit: Callable[[Doc], None], reason: str) -> None:
    assert run(name, edit, ACTIVE) == Reject(reason)


def test_plan_without_a_price_is_refused() -> None:
    catalog = Catalog({IND: "instructor"}, {"instructor": Offer("instructor", 1, None, None)})
    doc = load("transaction_completed_web")
    assert map_notification(body(doc), catalog, None) == Reject("price_missing")


def test_price_of_a_plan_not_in_the_offers_is_refused() -> None:
    catalog = Catalog({IND: "instructor"}, {})
    doc = load("transaction_completed_web")
    assert map_notification(body(doc), catalog, None) == Reject("unknown_price")


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b"null", b"\xff\xfe", b'{"a": 1'])
def test_not_a_json_object_is_refused(raw: bytes) -> None:
    assert map_notification(raw, CATALOG, None) == Reject("malformed")


def test_reasons_never_carry_body_values() -> None:
    secretish = "classbeat-unique-marker"
    for edit in (
        _set(["event_id"], secretish),
        _set(["data", "custom_data", "classbeat_owner_id"], secretish),
        _set([*TOTALS, "subtotal"], secretish),
    ):
        result = run("transaction_completed_web", edit)
        assert isinstance(result, Reject)
        assert secretish not in repr(result)


# --- the owner, read before the state is loaded ---------------------------------------------


def test_owner_of() -> None:
    assert owner_of(body(load("transaction_completed_web"))) == OWNER
    assert owner_of(body(load("subscription_canceled"))) == OWNER
    assert owner_of(b"{}") == Reject("malformed")
    assert owner_of(body({"data": {"custom_data": {}}})) == Reject("owner_missing")


# --- the mapped events pass the writer's rules ----------------------------------------------


def _quote(quote: Quote | object) -> Expected:
    assert isinstance(quote, Quote)
    return Expected(quote.amount_minor, quote.currency, quote.basis_minor)


def test_a_paddle_lifecycle_applies_through_decide() -> None:
    started = run("transaction_completed_web")
    assert isinstance(started, Mapped)
    first = decide(None, started.event, seen=False,
                   expected=_quote(quote_buy(OFFERS["instructor"], JAN31)))  # fmt: skip
    assert isinstance(first, Applied)
    assert first.state == ACTIVE

    upgrade = run("transaction_completed_update", state=first.state)
    assert isinstance(upgrade, Mapped)
    at = upgrade.event.period_start
    expected = _quote(quote_upgrade(current_of(first.state, 1), OFFERS["pro"], at))
    upgraded = decide(first.state, upgrade.event, seen=False, expected=expected)
    assert isinstance(upgraded, Applied)
    assert upgraded.state.plan_id == "pro"

    cancel = run("subscription_updated_cancel", state=upgraded.state)
    assert isinstance(cancel, Mapped)
    scheduled = decide(upgraded.state, cancel.event, seen=False)
    assert isinstance(scheduled, Applied)
    assert scheduled.state.cancel_at_end

    ended = run("subscription_canceled", state=scheduled.state)
    assert isinstance(ended, Mapped)
    final = decide(scheduled.state, ended.event, seen=False)
    assert isinstance(final, Applied)
    assert final.state.status == "ended"


def test_a_renewal_applies_through_decide() -> None:
    renewal = run("transaction_completed_recurring", state=ACTIVE)
    assert isinstance(renewal, Mapped)
    decision = decide(ACTIVE, renewal.event, seen=False,
                      expected=_quote(quote_buy(OFFERS["instructor"], FEB28)))  # fmt: skip
    assert isinstance(decision, Applied)
    assert (decision.state.period_start, decision.state.period_end) == (FEB28, MAR31)
