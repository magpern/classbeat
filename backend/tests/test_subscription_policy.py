"""Table-driven tests of the pure subscription policy (ADR 0031 section 5)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from classbeat.billing import subscription_policy as sp
from classbeat.billing.subscription_policy import Current, Offer, Quote, Refusal


def at(y: int, m: int, d: int, h: int = 12, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def cur(
    start: datetime,
    *,
    rank: int = 1,
    basis: int = 1000,
    plan: str = "instructor",
    nxt: str | None = None,
    cancel: bool = False,
) -> Current:
    a = start.day
    return Current(plan, rank, start, sp.period_end(start, a), a, basis, "USD", nxt, cancel)


def offer(
    plan: str = "pro", rank: int = 2, price: int | None = 3000, currency: str | None = "USD"
) -> Offer:
    return Offer(plan, rank, price, currency)


def quoted(result: Quote | Refusal) -> Quote:
    assert isinstance(result, Quote)
    return result


def test_a_quote_cannot_be_built_from_positional_arguments() -> None:
    """``kw_only=True`` on ``Quote`` (#1019): a reordered field can never silently swap values."""
    q = quoted(sp.quote_buy(offer(), at(2025, 3, 10)))
    args = tuple(getattr(q, name) for name in Quote.__dataclass_fields__)
    with pytest.raises(TypeError):
        Quote(*args)  # pyright: ignore[reportCallIssue]
    assert Quote(**{name: getattr(q, name) for name in Quote.__dataclass_fields__}) == q


@pytest.mark.parametrize(
    ("start", "anchor", "end"),
    [
        (at(2025, 1, 31), 31, at(2025, 2, 28)),
        (at(2025, 2, 28), 31, at(2025, 3, 31)),
        (at(2025, 3, 31), 31, at(2025, 4, 30)),
        (at(2025, 4, 30), 31, at(2025, 5, 31)),
        (at(2024, 1, 31), 31, at(2024, 2, 29)),  # leap year
        (at(2024, 2, 29), 31, at(2024, 3, 31)),
        (at(2024, 1, 29), 29, at(2024, 2, 29)),
        (at(2025, 1, 29), 29, at(2025, 2, 28)),
        (at(2025, 12, 15), 15, at(2026, 1, 15)),
        (at(2025, 12, 31, 23, 59), 31, at(2026, 1, 31, 23, 59)),
        (at(2025, 6, 10, 8, 30), 10, at(2025, 7, 10, 8, 30)),
    ],
)
def test_period_end(start: datetime, anchor: int, end: datetime) -> None:
    assert sp.period_end(start, anchor) == end


def test_no_drift_over_a_year_from_31_jan() -> None:
    start, ends = at(2025, 1, 31), []
    for _ in range(12):
        start = sp.period_end(start, 31)
        ends.append(start.day)
    assert ends == [28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31, 31]


@pytest.mark.parametrize("anchor", [0, 32, -1])
def test_period_end_rejects_bad_anchor(anchor: int) -> None:
    with pytest.raises(ValueError, match=r"anchor_day|naive"):
        sp.period_end(at(2025, 1, 1), anchor)


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError, match=r"anchor_day|naive"):
        sp.period_end(datetime(2025, 1, 1), 1)


def test_other_zones_are_read_as_utc() -> None:
    # 1 Feb 01:00 at +02:00 is 31 Jan 23:00 UTC: the month and day come from the UTC reading.
    plus2 = datetime(2025, 2, 1, 1, 0, tzinfo=timezone(timedelta(hours=2)))
    end = sp.period_end(plus2, 31)
    assert end == at(2025, 2, 28, 23)
    assert end.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("start", "days"),
    [(at(2025, 2, 1), 28), (at(2024, 2, 1), 29), (at(2025, 4, 1), 30), (at(2025, 1, 1), 31)],
)
def test_days_in_period(start: datetime, days: int) -> None:
    assert sp.days_in_period(start, sp.period_end(start, start.day)) == days


@pytest.mark.parametrize(
    ("now", "end", "left"),
    [
        (at(2025, 1, 1, 0, 0), at(2025, 2, 1), 31),
        (at(2025, 1, 31, 23, 59), at(2025, 2, 1), 1),
        (at(2025, 2, 1, 12), at(2025, 2, 1, 12), 1),  # same day: still one
        (at(2025, 2, 2), at(2025, 2, 1), 1),  # past the end: never below one
    ],
)
def test_days_left(now: datetime, end: datetime, left: int) -> None:
    assert sp.days_left(end, now) == left


@pytest.mark.parametrize(
    ("diff", "left", "total", "amount"),
    [
        (2000, 28, 28, 2000),  # first day of a 28-day month: the full difference
        (2000, 1, 28, 71),  # last day: 71.43 -> 71
        (2000, 29, 29, 2000),
        (2000, 1, 29, 69),  # 68.97 -> 69
        (2000, 30, 30, 2000),
        (2000, 1, 30, 67),  # 66.67 -> 67
        (2000, 31, 31, 2000),
        (2000, 1, 31, 65),  # 64.52 -> 65
        (1, 1, 2, 1),  # exactly half a cent rounds up
        (1, 1, 3, 0),  # a third rounds down
        (3, 1, 2, 2),  # 1.5 -> 2
        (5, 1, 10, 1),  # 0.5 -> 1
        (4, 1, 10, 0),  # 0.4 -> 0
        (1500, 15, 30, 750),
        (0, 10, 30, 0),
        (-500, 10, 30, 0),
    ],
)
def test_upgrade_amount_half_up(diff: int, left: int, total: int, amount: int) -> None:
    assert sp.upgrade_amount(diff, left, total) == amount


def test_amounts_are_never_negative_and_never_above_the_difference() -> None:
    for total in (28, 29, 30, 31):
        for left in range(1, total + 1):
            for diff in range(-50, 300):
                amount = sp.upgrade_amount(diff, left, total)
                assert 0 <= amount <= max(diff, 0)


def test_buy_starts_a_period_now_at_full_price() -> None:
    now = at(2025, 1, 31, 9, 5)
    q = quoted(sp.quote_buy(offer("instructor", 1, 1000), now))
    assert (q.kind, q.plan_id, q.amount_minor, q.currency) == ("buy", "instructor", 1000, "USD")
    assert (q.period_start, q.period_end) == (now, at(2025, 2, 28, 9, 5))
    assert (q.anchor_day, q.basis_minor, q.next_plan_id, q.cancel_at_end) == (31, 1000, None, False)


@pytest.mark.parametrize("o", [offer(price=None), offer(currency=None)])
def test_buy_needs_a_price(o: Offer) -> None:
    assert sp.quote_buy(o, at(2025, 1, 1)) == Refusal("price_missing")


@pytest.mark.parametrize(
    ("start", "now", "amount"),
    [
        (at(2025, 2, 1), at(2025, 2, 1, 0, 1), 2000),  # first day, 28-day month
        (at(2025, 2, 1), at(2025, 2, 28, 23, 59), 71),  # last day
        (at(2024, 2, 1), at(2024, 2, 29, 20), 69),
        (at(2025, 4, 1), at(2025, 4, 30, 20), 67),
        (at(2025, 1, 1), at(2025, 1, 1, 18), 2000),
        (at(2025, 1, 1), at(2025, 1, 31, 18), 65),
        (at(2025, 2, 1), at(2025, 2, 15, 6), 1000),  # 14 days left of 28: exact half
    ],
)
def test_upgrade_prorates_the_difference(start: datetime, now: datetime, amount: int) -> None:
    c = cur(start, basis=1000)
    q = quoted(sp.quote_upgrade(c, offer(price=3000), now))
    assert (q.kind, q.plan_id, q.amount_minor, q.currency) == ("upgrade", "pro", amount, "USD")
    assert (q.period_start, q.period_end) == (c.period_start, c.period_end)
    assert (q.anchor_day, q.basis_minor) == (c.anchor_day, 3000)


def test_upgrade_uses_the_stored_basis_not_the_current_price() -> None:
    c = cur(at(2025, 1, 1), basis=800)  # paid 8.00 though the plan now costs 10.00
    q = quoted(sp.quote_upgrade(c, offer(price=3000), at(2025, 1, 1)))
    assert q.amount_minor == 2200


@pytest.mark.parametrize("price", [1000, 900, 0])
def test_upgrade_at_or_below_the_basis_applies_free(price: int) -> None:
    c = cur(at(2025, 1, 1), basis=1000)
    q = quoted(sp.quote_upgrade(c, offer(price=price), at(2025, 1, 10)))
    assert (q.amount_minor, q.plan_id, q.basis_minor) == (0, "pro", price)


def test_upgrade_clears_a_scheduled_change() -> None:
    c = cur(at(2025, 1, 1), nxt="free", cancel=True)
    q = quoted(sp.quote_upgrade(c, offer(), at(2025, 1, 5)))
    assert (q.next_plan_id, q.cancel_at_end) == (None, False)


@pytest.mark.parametrize(
    ("o", "reason"),
    [
        (offer("instructor", 1), "not_an_upgrade"),
        (offer("free", 0, 0), "not_an_upgrade"),
        (offer(price=None), "price_missing"),
        (offer(currency=None), "price_missing"),
        (offer(currency="EUR"), "currency_mismatch"),
    ],
)
def test_upgrade_refusals(o: Offer, reason: str) -> None:
    result = sp.quote_upgrade(cur(at(2025, 1, 1)), o, at(2025, 1, 2))
    assert result == Refusal(reason)  # type: ignore[arg-type]


def test_downgrade_is_scheduled_at_amount_zero() -> None:
    c = cur(at(2025, 1, 1), rank=2, plan="pro", basis=3000)
    q = quoted(sp.quote_downgrade(c, offer("instructor", 1, 1000)))
    assert (q.kind, q.amount_minor) == ("downgrade", 0)
    assert (q.next_plan_id, q.cancel_at_end) == ("instructor", False)
    assert (q.plan_id, q.basis_minor, q.period_end) == ("pro", 3000, c.period_end)


@pytest.mark.parametrize(
    ("o", "reason"),
    [
        (offer("pro", 2), "not_a_downgrade"),
        (offer("x", 3), "not_a_downgrade"),
        (offer("instructor", 1, None), "price_missing"),
        (offer("instructor", 1, 1000, "EUR"), "currency_mismatch"),
    ],
)
def test_downgrade_refusals(o: Offer, reason: str) -> None:
    c = cur(at(2025, 1, 1), rank=2, plan="pro", basis=3000)
    assert sp.quote_downgrade(c, o) == Refusal(reason)  # type: ignore[arg-type]


def test_cancel_and_resume() -> None:
    c = cur(at(2025, 1, 1), nxt="free")
    cancel, resume = sp.quote_cancel(c), sp.quote_resume(c)
    assert (cancel.kind, cancel.amount_minor) == ("cancel", 0)
    assert (cancel.cancel_at_end, cancel.next_plan_id) == (True, None)
    assert (resume.kind, resume.amount_minor) == ("resume", 0)
    assert (resume.cancel_at_end, resume.next_plan_id) == (False, None)
    assert cancel.plan_id == resume.plan_id == "instructor"


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        (None, "cancel"),
        (offer("instructor", 1, 1000), "resume"),
        (offer("pro", 2), "upgrade"),
        (offer("free", 0, 0), "downgrade"),
    ],
)
def test_quote_change_routes_by_choice(target: Offer | None, kind: str) -> None:
    q = quoted(sp.quote_change(cur(at(2025, 1, 1), rank=1), target, at(2025, 1, 10)))
    assert q.kind == kind


def test_no_quote_is_ever_negative_across_a_range() -> None:
    start = at(2025, 1, 31)
    for basis in (0, 1, 999, 1000, 5000):
        c = cur(start, basis=basis)
        for day in range(28):
            now = start + timedelta(days=day, hours=5)
            for price in (0, 1, 500, 1000, 3000, 9999):
                results = (
                    sp.quote_upgrade(c, offer(price=price), now),
                    sp.quote_downgrade(cur(start, rank=3, basis=basis), offer("a", 1, price)),
                    sp.quote_cancel(c),
                    sp.quote_resume(c),
                    sp.quote_buy(offer(price=price), now),
                )
                assert all(quoted(q).amount_minor >= 0 for q in results)
