"""`classbeat.billing.apply_policy`, `choices` and `fast_forward` on in-memory state (#948). Pure:
no database. The writer's own tests (database) are in `test_billing_writer.py`."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from classbeat.billing.apply_policy import (
    Applied,
    Duplicate,
    Expected,
    Ignored,
    Refused,
    State,
    decide,
)
from classbeat.billing.choices import FREE_PLAN, change_quote, checkout_quote
from classbeat.billing.fast_forward import FastForward, plan_fast_forward
from classbeat.billing.periods import period_end
from classbeat.billing.provider import ChangeScheduled, Ended, Renewed, Started, Upgraded
from classbeat.billing.subscription_policy import Offer, Quote, Refusal

T0 = datetime(2026, 1, 31, 10, 0, tzinfo=UTC)
END = period_end(T0, 31)
STATE = State("dummy", "instructor", T0, END, 31, 1000, "USD")
PAID = Expected(1000, "USD", 1000)


def started(**kw: object) -> Started:
    fields: dict[str, object] = {
        "provider": "dummy", "event_id": "e1", "period_start": T0, "period_end": END,
        "plan_id": "instructor", "amount_minor": 1000, "currency": "USD", "anchor_day": 31,
    }  # fmt: skip
    return Started(**{**fields, **kw})  # pyright: ignore[reportArgumentType]


def renewed(**kw: object) -> Renewed:
    start = END
    fields: dict[str, object] = {
        "provider": "dummy", "event_id": "e2", "period_start": start,
        "period_end": period_end(start, 31), "plan_id": "instructor", "amount_minor": 1000,
        "currency": "USD",
    }  # fmt: skip
    return Renewed(**{**fields, **kw})  # pyright: ignore[reportArgumentType]


def test_a_replayed_event_is_a_duplicate_whatever_the_state() -> None:
    assert decide(None, started(), seen=True) == Duplicate()
    assert decide(STATE, renewed(), seen=True, expected=PAID) == Duplicate()


def test_a_started_event_with_the_right_amount_applies_and_grants_the_period() -> None:
    done = decide(None, started(), seen=False, expected=PAID)
    assert isinstance(done, Applied)
    assert (done.state.period_end, done.grant and done.grant.ends_at) == (END, END)


def test_a_paid_event_with_another_amount_or_currency_is_amount_mismatch() -> None:
    for kw in ({"amount_minor": 999}, {"currency": "EUR"}):
        assert decide(None, started(**kw), seen=False, expected=PAID) == Refused("amount_mismatch")
    assert decide(None, started(), seen=False, expected=None) == Refused("amount_mismatch")


def test_a_dummy_started_is_refused_while_a_real_provider_subscription_is_active() -> None:
    paddle = replace(STATE, provider="paddle")
    assert decide(paddle, started(), seen=False, expected=PAID) == Refused("provider_conflict")
    assert decide(paddle, renewed(), seen=False, expected=PAID) == Refused("provider_conflict")


def test_an_event_before_the_stored_period_is_ignored_as_stale() -> None:
    later = replace(STATE, period_start=END, period_end=period_end(END, 31))
    assert decide(later, started(), seen=False, expected=PAID) == Ignored()


def test_a_renewal_that_does_not_start_at_the_period_end_is_out_of_order() -> None:
    skipped = renewed(period_start=period_end(END, 31), period_end=period_end(END, 31))
    assert decide(STATE, skipped, seen=False, expected=PAID) == Refused("out_of_order")
    assert decide(None, renewed(), seen=False, expected=PAID) == Refused("out_of_order")


def test_a_renewal_moves_the_period_and_keeps_the_anchor_day() -> None:
    done = decide(STATE, renewed(), seen=False, expected=PAID)
    assert isinstance(done, Applied)
    assert (done.state.period_start, done.state.anchor_day) == (END, 31)


def test_a_cancelled_subscription_is_not_renewed_and_a_renewal_names_the_scheduled_plan() -> None:
    cancelled = replace(STATE, cancel_at_end=True)
    assert decide(cancelled, renewed(), seen=False, expected=PAID) == Refused("cancelled")
    down = replace(STATE, next_plan_id="free")
    assert decide(down, renewed(), seen=False, expected=PAID) == Refused("plan_mismatch")


def test_an_upgrade_keeps_the_period_and_an_upgrade_outside_it_is_refused() -> None:
    mid = datetime(2026, 2, 10, tzinfo=UTC)
    up = Upgraded(
        provider="dummy", event_id="u1", period_start=mid, period_end=END, plan_id="pro",
        amount_minor=700, currency="USD",
    )  # fmt: skip
    done = decide(STATE, up, seen=False, expected=Expected(700, "USD", 2500))
    assert isinstance(done, Applied)
    assert (done.state.period_end, done.state.basis_minor, done.state.plan_id) == (END, 2500, "pro")
    assert done.grant is not None
    assert done.grant.starts_at == mid
    moved = replace(up, period_end=period_end(END, 31))
    assert decide(STATE, moved, seen=False, expected=Expected(700, "USD", 2500)) == Refused(
        "period_mismatch"
    )


def test_a_change_never_carries_a_plan_and_a_cancel_together() -> None:
    both = ChangeScheduled(
        provider="dummy", event_id="c1", period_start=T0, period_end=END,
        next_plan_id="free", cancel_at_end=True,
    )  # fmt: skip
    assert decide(STATE, both, seen=False) == Refused("invalid_change")
    same = replace(both, next_plan_id="instructor", cancel_at_end=False)
    assert decide(STATE, same, seen=False) == Refused("invalid_change")


def test_a_change_for_another_period_is_out_of_order_and_ended_ends_the_subscription() -> None:
    other = ChangeScheduled(
        provider="dummy", event_id="c2", period_start=END, period_end=period_end(END, 31),
        cancel_at_end=True,
    )  # fmt: skip
    assert decide(STATE, other, seen=False) == Refused("out_of_order")
    ended = Ended(provider="dummy", event_id="d1", period_start=T0, period_end=END)
    done = decide(STATE, ended, seen=False)
    assert isinstance(done, Applied)
    assert done.state.status == "ended"
    assert done.grant is None


def test_the_anchor_day_follows_31_january_through_february_and_march() -> None:
    feb = period_end(T0, 31)
    mar = period_end(feb, 31)
    assert (feb.month, feb.day, mar.month, mar.day) == (2, 28, 3, 31)
    leap = period_end(datetime(2028, 1, 31, tzinfo=UTC), 31)
    assert (leap.month, leap.day) == (2, 29)
    second = decide(
        replace(STATE, period_end=feb), renewed(period_start=feb, period_end=mar),
        seen=False, expected=PAID,
    )  # fmt: skip
    assert isinstance(second, Applied)
    assert second.state.period_end == mar


def test_a_fast_forward_renewal_resets_the_anchor_to_the_new_start() -> None:
    start = datetime(2026, 2, 3, 9, tzinfo=UTC)
    state = replace(STATE, period_end=start)
    event = renewed(period_start=start, period_end=period_end(start, 3))
    assert decide(state, event, seen=False, expected=PAID) == Refused("period_mismatch")
    done = decide(state, event, seen=False, expected=PAID, reset_anchor=True)
    assert isinstance(done, Applied)
    assert done.state.anchor_day == 3


# --- the quotes (choices) ------------------------------------------------------------------

INSTRUCTOR = Offer("instructor", 2, 1000, "USD")
PRO = Offer("pro", 3, 2500, "USD")


def test_choosing_free_is_a_cancel_never_a_downgrade() -> None:
    assert FREE_PLAN == "free"
    quote = change_quote(STATE, 2, None, T0)
    assert isinstance(quote, Quote)
    assert (quote.kind, quote.cancel_at_end, quote.next_plan_id, quote.amount_minor) == (
        "cancel", True, None, 0,
    )  # fmt: skip


def test_a_quote_never_has_a_plan_and_a_cancel_and_never_a_negative_amount() -> None:
    now = datetime(2026, 2, 10, tzinfo=UTC)
    for target in (None, INSTRUCTOR, PRO, Offer("free", 1, 0, "USD")):
        quote = change_quote(STATE, 2, target, now)
        if isinstance(quote, Quote):
            assert not (quote.next_plan_id and quote.cancel_at_end)
            assert quote.amount_minor >= 0
    cheap = Offer("pro", 3, 500, "USD")  # a price below the basis still charges 0, not less
    quote = checkout_quote(STATE, 2, cheap, now)
    assert isinstance(quote, Quote)
    assert quote.amount_minor == 0


def test_a_checkout_outside_the_stored_period_or_on_the_same_plan_is_refused() -> None:
    assert checkout_quote(STATE, 2, PRO, END) == "outside_period"
    assert checkout_quote(STATE, 2, PRO, datetime(2025, 12, 1, tzinfo=UTC)) == "outside_period"
    assert checkout_quote(STATE, 2, INSTRUCTOR, T0) == "already_on_plan"
    assert isinstance(checkout_quote(None, None, INSTRUCTOR, T0), Quote)
    assert checkout_quote(None, None, Offer("pro", 3, None, None), T0) == Refusal("price_missing")


def test_a_change_without_a_subscription_or_to_a_higher_plan_is_refused() -> None:
    assert change_quote(None, None, None, T0) == "no_subscription"
    assert change_quote(STATE, 2, PRO, datetime(2026, 2, 10, tzinfo=UTC)) == "use_checkout"


# --- the fast-forward decision -------------------------------------------------------------


def test_a_fast_forward_is_refused_for_a_non_dummy_state_and_for_none() -> None:
    assert plan_fast_forward(replace(STATE, provider="paddle"), eligible=True) == Refused(
        "not_dummy"
    )
    assert plan_fast_forward(None, eligible=True) == Refused("no_subscription")
    assert plan_fast_forward(replace(STATE, status="ended"), eligible=True) == Refused(
        "no_subscription"
    )


@pytest.mark.parametrize(
    ("state", "eligible", "expected"),
    [
        (STATE, True, FastForward("renew", "instructor")),
        (replace(STATE, next_plan_id="free"), True, FastForward("renew", "free")),
        (replace(STATE, cancel_at_end=True), True, FastForward("end", "instructor")),
        (STATE, False, FastForward("end", "instructor")),
    ],
)
def test_a_fast_forward_renews_applies_the_scheduled_plan_or_ends(
    state: State, eligible: bool, expected: FastForward
) -> None:
    assert plan_fast_forward(state, eligible=eligible) == expected
