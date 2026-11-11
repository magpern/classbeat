"""The subscription writer and the billing tables against a real database (#948, ADR 0031): replay,
stale and out-of-order events, and what the app role and the CHECKs refuse. Pure rules are in
`test_billing_apply_policy.py`; the HTTP flow is in `test_billing_routes.py`."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.billing import writer
from classbeat.billing.apply_policy import Applied, Duplicate, Ignored
from classbeat.billing.errors import BillingRefused
from classbeat.billing.periods import period_end
from classbeat.billing.provider import ChangeScheduled, Renewed, Started, Upgraded
from plantools import grant, subscription_event

NOW = datetime.now(UTC).replace(microsecond=0)
Maint = async_sessionmaker[AsyncSession]


def new_user(conn: Connection) -> UUID:
    email = f"w-{secrets.token_hex(6)}@example.test"
    return conn.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at, role) "
            "VALUES (:e, :e, now(), 'user') RETURNING id"
        ),
        {"e": email},
    ).scalar_one()


def started(event_id: str = "dummy_a", **kw: Any) -> Started:
    fields: dict[str, Any] = {
        "provider": "dummy", "event_id": event_id, "period_start": NOW,
        "period_end": period_end(NOW, NOW.day), "plan_id": "instructor",
        "amount_minor": 1000, "currency": "USD", "anchor_day": NOW.day,
    }  # fmt: skip
    return Started(**{**fields, **kw})


async def run(maint: Maint, owner: UUID, event: Any, now: datetime = NOW) -> Any:
    async with maint() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ WRITE"))  # as `admin_session` does
        return await writer.apply(session, owner, event, now=now)


def count(conn: Connection, table: str, owner: UUID) -> int:
    return conn.execute(
        text(f"SELECT count(*) FROM {table} WHERE owner_id = :o"),  # noqa: S608
        {"o": owner},
    ).scalar_one()


@pytest.mark.usefixtures("priced")
async def test_a_replayed_event_is_a_duplicate_under_any_owner(
    maint: Maint, superuser: Connection
) -> None:
    first, other = new_user(superuser), new_user(superuser)
    assert isinstance(await run(maint, first, started()), Applied)
    assert isinstance(await run(maint, first, started()), Duplicate)
    assert isinstance(await run(maint, other, started()), Duplicate)
    assert count(superuser, "billing_events", first) == 1
    assert count(superuser, "billing_events", other) == 0
    assert count(superuser, "entitlements", other) == 0


@pytest.mark.usefixtures("priced")
async def test_a_stale_event_is_recorded_as_ignored(maint: Maint, superuser: Connection) -> None:
    owner = new_user(superuser)
    await run(maint, owner, started())
    old = NOW - timedelta(days=40)
    stale = Renewed(
        provider="dummy", event_id="dummy_old", period_start=old,
        period_end=period_end(old, old.day), plan_id="instructor",
        amount_minor=1000, currency="USD",
    )  # fmt: skip
    assert isinstance(await run(maint, owner, stale), Ignored)
    outcome = superuser.execute(
        text("SELECT outcome FROM billing_events WHERE event_id = 'dummy_old'")
    ).scalar_one()
    assert outcome == "ignored"
    assert count(superuser, "entitlements", owner) == 1


@pytest.mark.usefixtures("priced")
async def test_an_out_of_order_renewal_records_nothing(maint: Maint, superuser: Connection) -> None:
    owner = new_user(superuser)
    await run(maint, owner, started())
    start = NOW + timedelta(days=90)  # not the stored period end
    early = Renewed(
        provider="dummy", event_id="dummy_early", period_start=start,
        period_end=period_end(start, NOW.day), plan_id="instructor",
        amount_minor=1000, currency="USD",
    )  # fmt: skip
    with pytest.raises(BillingRefused) as refused:
        await run(maint, owner, early)
    assert refused.value.reason == "out_of_order"
    assert count(superuser, "billing_events", owner) == 1


@pytest.mark.usefixtures("priced")
async def test_the_writer_checks_the_amount_and_the_period(
    maint: Maint, superuser: Connection
) -> None:
    owner = new_user(superuser)
    with pytest.raises(BillingRefused) as cheap:
        await run(maint, owner, started(amount_minor=1))
    assert cheap.value.reason == "amount_mismatch"
    await run(maint, owner, started())
    later = NOW + timedelta(days=40)  # the upgrade's "now" is past the period
    upgrade = Upgraded(
        provider="dummy", event_id="dummy_up", period_start=later,
        period_end=period_end(NOW, NOW.day), plan_id="pro", amount_minor=1500, currency="USD",
    )  # fmt: skip
    with pytest.raises(BillingRefused) as outside:
        await run(maint, owner, upgrade, later)
    assert outside.value.reason == "outside_period"
    assert count(superuser, "billing_events", owner) == 1


@pytest.mark.usefixtures("priced")
async def test_a_schedule_change_moves_no_entitlement(maint: Maint, superuser: Connection) -> None:
    owner = new_user(superuser)
    event = await run(maint, owner, started())
    assert isinstance(event, Applied)
    cancel = ChangeScheduled(
        provider="dummy", event_id="dummy_c", period_start=NOW,
        period_end=period_end(NOW, NOW.day), cancel_at_end=True,
    )  # fmt: skip
    await run(maint, owner, cancel)
    assert count(superuser, "entitlements", owner) == 1


def refusal(conn: Connection, sql: str, **params: Any) -> str:
    """The database's refusal text of `sql`."""
    with pytest.raises(DBAPIError) as err:
        conn.execute(text(sql), params)
    return str(err.value.orig)


def test_the_app_role_cannot_write_or_read_billing(
    app_database_url: str, superuser: Connection
) -> None:
    owner = new_user(superuser)
    event = subscription_event(superuser, owner, "instructor", NOW, NOW + timedelta(days=30))
    engine = create_engine(app_database_url)
    try:
        for sql in (
            "INSERT INTO billing_events (owner_id, provider, event_id, kind, period_start, "
            "period_end, outcome) VALUES (:o, 'dummy', 'x', 'started', now(), now(), 'applied')",
            "INSERT INTO entitlements (owner_id, plan_id, source, starts_at, ends_at, "
            "billing_event_id) VALUES (:o, 'instructor', 'subscription', now(), "
            "now() + interval '30 days', :e)",
            "INSERT INTO entitlements (owner_id, features, source, ends_at) "
            "VALUES (:o, '{billing_test}', 'grant', now() + interval '30 days')",
            "SELECT monthly_price_cents FROM plans",
            "SELECT amount_minor FROM billing_events",
        ):
            with engine.connect() as conn:
                text_ = refusal(conn, sql, o=owner, e=event)
                assert "permission denied" in text_ or "row-level security" in text_, sql
    finally:
        engine.dispose()


def _subscription_grant(conn: Connection, owner: UUID, **columns: Any) -> None:
    grant(conn, owner, columns.pop("plan_id", "instructor"), **columns)


def test_the_checks_hold_even_for_the_maint_role(superuser: Connection) -> None:
    owner = new_user(superuser)
    end = NOW + timedelta(days=30)
    event = subscription_event(superuser, owner, "instructor", NOW, end)
    base: dict[str, Any] = {
        "source": "subscription", "starts_at": NOW, "ends_at": end, "billing_event_id": event,
    }  # fmt: skip
    for bad in (
        {"billing_event_id": None},
        {"ends_at": NOW + timedelta(days=33)},
        {"features": ["billing_test"]},
        {"plan_id": None, "features": ["billing_test"]},
    ):
        with pytest.raises(DBAPIError):
            _subscription_grant(superuser, owner, **{**base, **bad})
    assert count(superuser, "entitlements", owner) == 0
    _subscription_grant(superuser, owner, **base)
    assert count(superuser, "entitlements", owner) == 1


def test_a_billing_test_grant_is_limited_to_90_days(superuser: Connection) -> None:
    owner = new_user(superuser)
    for days, ok in ((89, True), (120, False)):
        sql = (
            "INSERT INTO entitlements (owner_id, features, source, ends_at) "
            "VALUES (:o, '{billing_test}', 'grant', now() + make_interval(days => :d))"
        )
        if ok:
            superuser.execute(text(sql), {"o": owner, "d": days})
        else:
            with pytest.raises(DBAPIError):
                superuser.execute(text(sql), {"o": owner, "d": days})
