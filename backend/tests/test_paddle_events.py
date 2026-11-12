"""Paddle notifications to neutral events (ADR 0032 sections 1, 3 and 5, #972): no I/O.

Fixtures (`tests/fixtures/paddle/`) follow Paddle's public example payloads; each case edits a
copy. The user is never taken from the payload: every case passes the owner and the `Link` a
route would resolve. Both providers run every source. The last tests feed the mapped events to
`apply_policy.decide`, the writer's rules.
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
from classbeat.billing.paddle_events import map_notification, parse, refs
from classbeat.billing.paddle_types import (
    Catalog,
    Link,
    Mapped,
    Notification,
    Refs,
    Reject,
    Skip,
    Stored,
)
from classbeat.billing.provider import ChangeScheduled, Ended, Renewed, Started, Upgraded
from classbeat.billing.subscription_policy import Offer, Quote, quote_buy, quote_upgrade

FIXTURES = Path(__file__).parent / "fixtures" / "paddle"
OWNER = UUID("7d3c1f0e-5b9a-4c2d-8e6f-1a2b3c4d5e6f")
OTHER_OWNER = UUID("00000000-0000-4000-8000-000000000001")
SUB = "sub_01hv8x2a3b4c5d6e7f8g9h0j1k"
OTHER_SUB = "sub_01hv8xotherotherotherother"
CHECKOUT = "txn_01hv8xb1b1b1b1b1b1b1b1b1b1"
CUSTOMER = "ctm_01hv8x0c1d2e3f4g5h6j7k8m9n"
IND, PRO = "pri_01hv8wzaaaaaaaaaaaaaaaaaaa", "pri_01hv8wzbbbbbbbbbbbbbbbbbbb"
OFFERS = {
    "free": Offer("free", 0, None, None),
    "instructor": Offer("instructor", 1, 1000, "USD"),
    "pro": Offer("pro", 2, 3000, "USD"),
}
CATALOG = Catalog({IND: "instructor", PRO: "pro"}, OFFERS)
JAN31 = datetime(2026, 1, 31, 10, tzinfo=UTC)
FEB8 = datetime(2026, 2, 8, 12, tzinfo=UTC)
FEB10 = datetime(2026, 2, 10, 9, tzinfo=UTC)  # the cancel fixture's occurred_at
FEB11 = datetime(2026, 2, 11, 9, tzinfo=UTC)  # the resume fixture's occurred_at
FEB28 = datetime(2026, 2, 28, 10, tzinfo=UTC)
MAR31 = datetime(2026, 3, 31, 10, tzinfo=UTC)
LATER = datetime(2026, 2, 28, 11, tzinfo=UTC)  # a newer subscription's period start
PROVIDERS = ["paddle", "fake_paddle"]

Doc = dict[str, Any]
Edit = Callable[[Doc], None]


def state(provider: str = "paddle", **changes: Any) -> State:
    values: dict[str, Any] = {
        "provider": provider, "plan_id": "instructor", "period_start": JAN31,
        "period_end": FEB28, "anchor_day": 31, "basis_minor": 1000, "basis_currency": "USD",
    } | changes  # fmt: skip
    return State(**values)


def link(provider: str = "paddle", **changes: Any) -> Link:
    values: dict[str, Any] = {
        "provider": provider, "checkout_ref": CHECKOUT, "customer_ref": CUSTOMER,
        "subscription_ref": SUB,
    } | changes  # fmt: skip
    return Link(**values)


def load(name: str) -> Doc:
    return json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))


def body(doc: Doc) -> bytes:
    return json.dumps(doc).encode()


def notification(doc: Doc) -> Notification:
    n = parse(body(doc))
    assert isinstance(n, Notification)
    return n


def run(
    name: str,
    edit: Edit | None = None,
    stored: State | Stored | None = None,
    *,
    provider: str = "paddle",
    the_link: Link | None = None,
    owner: UUID = OWNER,
) -> Mapped | Skip | Reject:
    doc = copy.deepcopy(load(name))
    if edit is not None:
        edit(doc)
    n = parse(body(doc))
    if not isinstance(n, Notification):
        return n
    held = Stored(stored) if isinstance(stored, State) else stored
    return map_notification(
        n, owner_id=owner, provider=provider, catalog=CATALOG, stored=held,
        link=the_link or link(provider),
    )  # fmt: skip


def _walk(doc: Doc, path: list[str | int]) -> Any:
    target: Any = doc
    for key in path[:-1]:
        target = target[key]
    return target


def _set(path: list[str | int], value: object) -> Edit:
    def edit(doc: Doc) -> None:
        _walk(doc, path)[path[-1]] = value

    return edit


def _drop(path: list[str | int]) -> Edit:
    def edit(doc: Doc) -> None:
        del _walk(doc, path)[path[-1]]

    return edit


# --- each source maps to one neutral event, for both providers ------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_first_payment_is_started(provider: str) -> None:
    assert run("transaction_completed_web", provider=provider) == Mapped(
        Started(
            provider=provider, event_id="evt_01hv8xa1a1a1a1a1a1a1a1a1a1", period_start=JAN31,
            period_end=FEB28, plan_id="instructor", amount_minor=1000, currency="USD",
            anchor_day=31,
        ),
        SUB,
    )  # fmt: skip


@pytest.mark.parametrize("provider", PROVIDERS)
def test_first_payment_replaces_a_dummy_subscription(provider: str) -> None:
    mapped = run("transaction_completed_web", None, state("dummy"), provider=provider)
    assert isinstance(mapped, Mapped)
    assert isinstance(mapped.event, Started)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_recurring_payment_is_renewed(provider: str) -> None:
    got = run("transaction_completed_recurring", None, state(provider), provider=provider)
    assert got == Mapped(
        Renewed(
            provider=provider, event_id="evt_01hv8xa2a2a2a2a2a2a2a2a2a2", period_start=FEB28,
            period_end=MAR31, plan_id="instructor", amount_minor=1000, currency="USD",
        ),
        SUB,
    )  # fmt: skip


@pytest.mark.parametrize("provider", PROVIDERS)
def test_proration_charge_is_upgraded(provider: str) -> None:
    got = run("transaction_completed_update", None, state(provider), provider=provider)
    assert got == Mapped(
        Upgraded(
            provider=provider, event_id="evt_01hv8xa3a3a3a3a3a3a3a3a3a3", period_start=FEB8,
            period_end=FEB28, plan_id="pro", amount_minor=1429, currency="USD",
        ),
        SUB,
    )  # fmt: skip


@pytest.mark.parametrize("provider", PROVIDERS)
def test_scheduled_cancel_is_change_scheduled(provider: str) -> None:
    got = run("subscription_updated_cancel", None, state(provider), provider=provider)
    assert got == Mapped(
        ChangeScheduled(
            provider=provider, event_id="evt_01hv8xa4a4a4a4a4a4a4a4a4a4", period_start=JAN31,
            period_end=FEB28, cancel_at_end=True, occurred_at=FEB10,
        ),
        SUB,
    )  # fmt: skip


@pytest.mark.parametrize("provider", PROVIDERS)
def test_removed_cancel_is_a_resume(provider: str) -> None:
    stored = state(provider, cancel_at_end=True)
    assert run("subscription_updated_resume", None, stored, provider=provider) == Mapped(
        ChangeScheduled(
            provider=provider, event_id="evt_01hv8xa5a5a5a5a5a5a5a5a5a5", period_start=JAN31,
            period_end=FEB28, cancel_at_end=False, occurred_at=FEB11,
        ),
        SUB,
    )  # fmt: skip


def test_a_cancel_replaces_a_scheduled_downgrade() -> None:
    mapped = run("subscription_updated_cancel", None, state(next_plan_id="free"))
    assert isinstance(mapped, Mapped)
    assert isinstance(mapped.event, ChangeScheduled)
    assert (mapped.event.next_plan_id, mapped.event.cancel_at_end) == (None, True)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_canceled_is_ended_for_the_stored_period(provider: str) -> None:
    assert run("subscription_canceled", None, state(provider), provider=provider) == Mapped(
        Ended(
            provider=provider, event_id="evt_01hv8xa6a6a6a6a6a6a6a6a6a6", period_start=JAN31,
            period_end=FEB28,
        ),
        SUB,
    )  # fmt: skip


@pytest.mark.parametrize("provider", PROVIDERS)
def test_event_id_is_paddles_notification_id_on_every_source(provider: str) -> None:
    for name in (
        "transaction_completed_recurring",
        "transaction_completed_update",
        "subscription_updated_cancel",
        "subscription_canceled",
    ):
        mapped = run(name, None, state(provider), provider=provider)
        assert isinstance(mapped, Mapped), name
        assert mapped.event.event_id == load(name)["event_id"], name


def test_mapping_is_deterministic_for_a_retried_delivery() -> None:
    retried = _set(["notification_id"], "ntf_01hv8xzzzzzzzzzzzzzzzzzzzz")
    assert run("transaction_completed_web", retried) == run("transaction_completed_web")


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="provider"):
        run("transaction_completed_web", provider="dummy")


# --- who it is for: only the link, `custom_data` a cross-check ------------------------------


def test_refs() -> None:
    txn = notification(load("transaction_completed_web"))
    assert refs(txn) == Refs(CHECKOUT, SUB, CUSTOMER, str(OWNER))
    sub = notification(load("subscription_canceled"))
    assert refs(sub) == Refs(None, SUB, CUSTOMER, str(OWNER))


def test_refs_without_custom_data() -> None:
    doc = load("transaction_completed_web")
    doc["data"]["custom_data"] = None
    assert refs(notification(doc)) == Refs(CHECKOUT, SUB, CUSTOMER, None)


@pytest.mark.parametrize(
    "name", ["transaction_completed_web", "subscription_updated_cancel", "subscription_canceled"]
)
@pytest.mark.parametrize("custom", [None, {}])
def test_no_custom_data_still_maps_through_the_link(name: str, custom: object) -> None:
    stored = None if name == "transaction_completed_web" else state()
    assert isinstance(run(name, _set(["data", "custom_data"], custom), stored), Mapped)


def test_owner_comes_from_the_caller_not_the_payload() -> None:
    # Without the cross-check value, the event maps for whichever owner the link named: nothing
    # in the payload chose the user, and nothing in the result names one.
    no_custom = _set(["data", "custom_data"], None)
    assert run("transaction_completed_web", no_custom, owner=OTHER_OWNER) == run(
        "transaction_completed_web", no_custom
    )


@pytest.mark.parametrize(
    ("name", "edit", "the_link", "owner"),
    [
        ("transaction_completed_web", None, None, OTHER_OWNER),  # custom_data disagrees
        ("transaction_completed_web",
         _set(["data", "custom_data", "classbeat_owner_id"], "not-a-uuid"), None, OWNER),
        ("transaction_completed_web",
         _set(["data", "custom_data", "classbeat_owner_id"], 7), None, OWNER),
        ("transaction_completed_web", None, link(checkout_ref="txn_01hv8xotherotherother"),
         OWNER),
        ("transaction_completed_web", None, link(checkout_ref=None), OWNER),
        ("transaction_completed_web", None, link(customer_ref="ctm_01hv8xotherotherother"),
         OWNER),
        ("transaction_completed_web", None, link(subscription_ref=OTHER_SUB), OWNER),
        ("transaction_completed_recurring", None, link(subscription_ref=OTHER_SUB), OWNER),
        ("transaction_completed_web", None, link("fake_paddle"), OWNER),  # the other provider's
        ("subscription_updated_cancel", None, link(subscription_ref=None), OWNER),
        ("subscription_updated_cancel", None, link(subscription_ref=OTHER_SUB), OWNER),
        # a second subscription's cancel never ends the linked one
        ("subscription_canceled", None, link(subscription_ref=OTHER_SUB), OWNER),
        ("subscription_canceled", _set(["data", "id"], OTHER_SUB), None, OWNER),
        ("subscription_canceled", None, link(subscription_ref=None), OWNER),
        ("subscription_canceled", None, None, OTHER_OWNER),
    ],
)  # fmt: skip
def test_link_mismatch_is_refused(
    name: str, edit: Edit | None, the_link: Link | None, owner: UUID
) -> None:
    assert run(name, edit, state(), the_link=the_link, owner=owner) == Reject("link_mismatch")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_cancel_with_another_subscription_id_is_refused_for_both_providers(provider: str) -> None:
    other = _set(["data", "id"], OTHER_SUB)
    got = run("subscription_canceled", other, state(provider), provider=provider)
    assert got == Reject("link_mismatch")


# --- subscription.updated: state-based and ordered by occurred_at ---------------------------


def test_update_as_old_as_the_last_change_is_stale() -> None:
    stored = Stored(state(), changed_at=FEB10)
    assert run("subscription_updated_cancel", None, stored) == Skip("stale_change")


def test_newer_update_that_changes_nothing_reports_its_time() -> None:
    got = run("subscription_updated_resume", None, state())
    assert got == Skip("no_change", changed_at=FEB11)


def test_reversed_cancel_and_resume_end_on_the_resume() -> None:
    """Paddle: cancel on 10 Feb, resume on 11 Feb; delivered resume first, then cancel."""
    resume = run("subscription_updated_resume", None, state())
    assert resume == Skip("no_change", changed_at=FEB11)
    # The route stores the reported time; the older cancel then changes nothing.
    stored = Stored(state(), changed_at=FEB11)
    assert run("subscription_updated_cancel", None, stored) == Skip("stale_change")


def test_reversed_cancel_and_resume_on_a_stored_cancel() -> None:
    """Stored: cancelled (an earlier change). Paddle: resume on 10 Feb, cancel again on
    11 Feb; delivered cancel first (no change, time reported), then the older resume."""
    first = state(cancel_at_end=True)
    cancel_again = _set(["occurred_at"], "2026-02-11T09:00:00Z")
    assert run("subscription_updated_cancel", cancel_again, first) == Skip(
        "no_change", changed_at=FEB11
    )
    older_resume = _set(["occurred_at"], "2026-02-10T09:00:00Z")
    stored = Stored(first, changed_at=FEB11)
    assert run("subscription_updated_resume", older_resume, stored) == Skip("stale_change")


def test_in_order_cancel_then_resume() -> None:
    cancel = run("subscription_updated_cancel", None, state())
    assert isinstance(cancel, Mapped)
    assert isinstance(cancel.event, ChangeScheduled)
    stored = Stored(state(cancel_at_end=True), changed_at=cancel.event.occurred_at)
    resume = run("subscription_updated_resume", None, stored)
    assert isinstance(resume, Mapped)
    assert isinstance(resume.event, ChangeScheduled)
    assert not resume.event.cancel_at_end


@pytest.mark.parametrize("status", ["canceled", "past_due", "paused"])
def test_update_with_a_non_live_status_is_skipped(status: str) -> None:
    def edit(doc: Doc) -> None:
        doc["data"].update(status=status, current_billing_period=None, scheduled_change=None)

    got = run("subscription_updated_cancel", edit, state(cancel_at_end=True))
    assert got == Skip("not_active")


def test_update_before_any_stored_subscription_goes_to_the_writer() -> None:
    # No stored state yet (the first payment is still on its way): the writer refuses it as
    # out of order, which Paddle retries.
    assert isinstance(run("subscription_updated_cancel"), Mapped)


# --- acknowledged and ignored ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "edit", "stored", "provider", "reason"),
    [
        ("subscription_created", None, None, "paddle", "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "subscription.activated"), None, "paddle",
         "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "subscription.past_due"), None, "paddle",
         "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "transaction.paid"), None, "paddle",
         "event_type_ignored"),
        ("subscription_created", _set(["event_type"], "transaction.payment_failed"), None,
         "paddle", "payment_failed"),
        ("adjustment_created", None, None, "paddle", "refund_policy_pending"),
        ("adjustment_created", _set(["event_type"], "adjustment.updated"), None, "paddle",
         "refund_policy_pending"),
        ("transaction_completed_web", _set(["data", "subscription_id"], None), None, "paddle",
         "not_a_subscription"),
        ("transaction_completed_web", _set(["data", "origin"], "subscription_charge"), None,
         "paddle", "origin_ignored"),
        ("transaction_completed_web",
         _set(["data", "origin"], "subscription_payment_method_change"), None, "paddle",
         "origin_ignored"),
        ("subscription_updated_cancel", _set(["data", "scheduled_change", "action"], "pause"),
         state(), "paddle", "change_not_supported"),
        ("subscription_updated_cancel", None, state(status="ended"), "paddle",
         "no_active_subscription"),
        ("subscription_canceled", None, None, "paddle", "no_active_subscription"),
        ("subscription_canceled", None, state("dummy"), "paddle", "no_active_subscription"),
        ("subscription_canceled", None, state("fake_paddle"), "paddle",
         "no_active_subscription"),
        ("subscription_canceled", None, state("paddle"), "fake_paddle",
         "no_active_subscription"),
        ("subscription_canceled", None, state(status="ended"), "paddle",
         "no_active_subscription"),
        ("subscription_canceled", None, state(period_start=LATER, period_end=MAR31), "paddle",
         "stale"),
        ("subscription_canceled", None, state("fake_paddle", period_start=LATER,
         period_end=MAR31), "fake_paddle", "stale"),
    ],
)  # fmt: skip
def test_skipped(
    name: str, edit: Edit | None, stored: State | None, provider: str, reason: str
) -> None:
    assert run(name, edit, stored, provider=provider) == Skip(reason)


def test_ignored_types_need_no_valid_data() -> None:
    assert parse(body({"event_type": "subscription.created"})) == Skip("event_type_ignored")


# --- refused: nothing written, no retry fixes it --------------------------------------------


def _two_items(doc: Doc) -> None:
    doc["data"]["items"].append(copy.deepcopy(doc["data"]["items"][0]))


PRICE: list[str | int] = ["data", "items", 0, "price"]
TOTALS: list[str | int] = ["data", "details", "totals"]
PERIOD: list[str | int] = ["data", "billing_period"]


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
        ("transaction_completed_web", _set([*PERIOD, "ends_at"], "2026-03-03T10:00:00Z"),
         "period_mismatch"),
        ("transaction_completed_web", _set([*PERIOD, "ends_at"], "2026-02-28T10:00:01Z"),
         "period_mismatch"),
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
        # periods Paddle must send
        ("transaction_completed_web", _set(PERIOD, None), "period_missing"),
        ("transaction_completed_update", _drop(["data", "items", 0, "proration"]), "malformed"),
        ("transaction_completed_update",
         _set(["data", "items", 0, "proration", "billing_period"], None), "period_missing"),
        ("subscription_updated_cancel", _set(["data", "current_billing_period"], None),
         "period_missing"),
        # malformed fields
        ("transaction_completed_web", _drop(["event_id"]), "malformed"),
        # the neutral id is the notification's evt_ id, nothing else
        ("transaction_completed_web", _set(["event_id"], CHECKOUT), "malformed"),
        ("transaction_completed_web", _set(["event_id"], "evt_01H-BAD"), "malformed"),
        ("transaction_completed_web", _set(["event_id"], "evt_"), "malformed"),
        ("transaction_completed_web", _set(["event_id"], "evt_" + "a" * 65), "malformed"),
        ("transaction_completed_web", _drop(["occurred_at"]), "malformed"),
        ("transaction_completed_web", _set(["event_type"], 7), "malformed"),
        ("transaction_completed_web", _set(["data"], []), "malformed"),
        ("transaction_completed_web", _set(["data", "custom_data"], []), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], "-1000"), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], 1000), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], "10.00"), "malformed"),
        ("transaction_completed_web", _set([*TOTALS, "subtotal"], "9" * 16), "malformed"),
        ("transaction_completed_web", _set([*PERIOD, "starts_at"], "2026-01-31T10:00:00"),
         "malformed"),
        ("transaction_completed_web", _set([*PERIOD, "starts_at"], "yesterday"), "malformed"),
        ("transaction_completed_web", _set([*PERIOD, "starts_at"], "2026-02-28T10:00:00Z"),
         "malformed"),
        ("transaction_completed_web", _set(["data", "currency_code"], "usd"), "malformed"),
        ("transaction_completed_web", _set(["data", "subscription_id"], "sub 1"), "malformed"),
        ("transaction_completed_web", _set(["data", "customer_id"], 7), "malformed"),
        ("subscription_canceled", _set(["data", "canceled_at"], None), "malformed"),
        ("subscription_updated_cancel", _drop(["data", "status"]), "malformed"),
    ],
)  # fmt: skip
def test_rejected(name: str, edit: Edit, reason: str) -> None:
    stored = None if name == "transaction_completed_web" else state()
    assert run(name, edit, stored) == Reject(reason)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    ("name", "held", "reason"),
    [
        # by origin and stored state: permanent refusals, so the route answers 422, not 409
        ("transaction_completed_recurring", None, "no_active_subscription"),
        ("transaction_completed_recurring", "ended", "no_active_subscription"),
        ("transaction_completed_update", None, "no_active_subscription"),
        ("transaction_completed_recurring", "dummy", "provider_conflict"),
        ("transaction_completed_update", "dummy", "provider_conflict"),
        ("transaction_completed_web", "same", "already_active"),
        ("subscription_updated_cancel", "dummy", "provider_conflict"),
    ],
)
def test_refused_by_stored_state(provider: str, name: str, held: str | None, reason: str) -> None:
    stored = {
        None: None, "ended": state(provider, status="ended"), "dummy": state("dummy"),
        "same": state(provider),
    }[held]  # fmt: skip
    assert run(name, None, stored, provider=provider) == Reject(reason)


def test_the_other_paddle_provider_is_a_conflict_too() -> None:
    got = run("transaction_completed_recurring", None, state("paddle"), provider="fake_paddle")
    assert got == Reject("provider_conflict")


def test_plan_without_a_price_is_refused() -> None:
    catalog = Catalog({IND: "instructor"}, {"instructor": Offer("instructor", 1, None, None)})
    n = notification(load("transaction_completed_web"))
    got = map_notification(
        n, owner_id=OWNER, provider="paddle", catalog=catalog, stored=None, link=link()
    )
    assert got == Reject("price_missing")


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json", b"[]", b"null", b"\xff\xfe", b'{"a": 1', b"[" * 100_000 + b"]" * 100_000],
)
def test_not_a_json_object_is_refused(raw: bytes) -> None:
    assert parse(raw) == Reject("malformed")


def test_reasons_never_carry_body_values() -> None:
    marker = "classbeat-unique-marker"
    for edit in (
        _set(["event_id"], marker),
        _set(["data", "custom_data", "classbeat_owner_id"], marker),
        _set([*TOTALS, "subtotal"], marker),
    ):
        result = run("transaction_completed_web", edit)
        assert isinstance(result, Reject)
        assert marker not in repr(result)


# --- the mapped events pass the writer's rules ----------------------------------------------


def _expected(quote: Quote | object) -> Expected:
    assert isinstance(quote, Quote)
    return Expected(quote.amount_minor, quote.currency, quote.basis_minor)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_paddle_lifecycle_applies_through_decide(provider: str) -> None:
    started = run("transaction_completed_web", provider=provider)
    assert isinstance(started, Mapped)
    buy = _expected(quote_buy(OFFERS["instructor"], JAN31))
    first = decide(None, started.event, seen=False, expected=buy)
    assert isinstance(first, Applied)
    assert first.state == state(provider)

    upgrade = run("transaction_completed_update", None, first.state, provider=provider)
    assert isinstance(upgrade, Mapped)
    proration = quote_upgrade(current_of(first.state, 1), OFFERS["pro"], FEB8)
    upgraded = decide(first.state, upgrade.event, seen=False, expected=_expected(proration))
    assert isinstance(upgraded, Applied)
    assert upgraded.state.plan_id == "pro"

    cancel = run("subscription_updated_cancel", None, upgraded.state, provider=provider)
    assert isinstance(cancel, Mapped)
    scheduled = decide(upgraded.state, cancel.event, seen=False)
    assert isinstance(scheduled, Applied)
    assert scheduled.state.cancel_at_end

    ended = run("subscription_canceled", None, scheduled.state, provider=provider)
    assert isinstance(ended, Mapped)
    final = decide(scheduled.state, ended.event, seen=False)
    assert isinstance(final, Applied)
    assert final.state.status == "ended"


def test_a_renewal_applies_through_decide() -> None:
    renewal = run("transaction_completed_recurring", None, state())
    assert isinstance(renewal, Mapped)
    expected = _expected(quote_buy(OFFERS["instructor"], FEB28))
    decision = decide(state(), renewal.event, seen=False, expected=expected)
    assert isinstance(decision, Applied)
    assert (decision.state.period_start, decision.state.period_end) == (FEB28, MAR31)
