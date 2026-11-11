"""Helpers for the plan, redeem and trial tests (PL1, #212): the superuser connection that plays
the admin (codes, grants, settings, ledger rows), and planted codes. Public test values only."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat.plans.codes import new_code
from jobtools import engine_for


@pytest.fixture
def users_plan() -> str | None:
    """The plan the jobs-API test users are granted (``browsers``, ``songenv.rider``; QT1, #214).

    ``pro`` (100 classes, 25 revisions, 3 jobs at once under ``CLASSBEAT_JOBS_PER_USER``'s 2)
    gives the limits these tests had before M5, through the real path: an admin grant that
    ``classbeat.plans.resolve`` reads and the metered ledger counts. A module that tests the free
    plan opts out by overriding this fixture with ``None``."""
    return "pro"


@pytest.fixture
def admin(env: Any, database_url: str) -> Iterator[Connection]:
    """The superuser (test setup only), with no ``trial`` setting before or after the test."""
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        connection.execute(text("DELETE FROM app_settings"))
        try:
            yield connection
        finally:
            connection.execute(text("DELETE FROM app_settings"))


def plant_code(
    conn: Connection,
    *,
    plan: str | None = "instructor",
    features: tuple[str, ...] = (),
    days: int = 30,
    uses: int = 5,
    used: int = 0,
    expires: str = "now() + interval '1 day'",
    disabled: bool = False,
) -> tuple[str, UUID]:
    """A redeem code as the admin would create it: (the code shown once, its row id)."""
    code, digest = new_code()
    code_id = conn.execute(
        text(
            "INSERT INTO redeem_codes (code_hash, label, plan_id, features, grant_days, "  # noqa: S608
            f"max_redemptions, redeemed_count, expires_at, disabled_at) VALUES (:h, 'test', :p, "
            f":f, :d, :m, :c, {expires}, {'now()' if disabled else 'NULL'}) RETURNING id"
        ),
        {"h": digest, "p": plan, "f": list(features), "d": days, "m": uses, "c": used},
    ).scalar_one()
    return code, code_id


def redeemed_count(conn: Connection, code_id: UUID) -> int:
    return conn.execute(
        text("SELECT redeemed_count FROM redeem_codes WHERE id = :c"), {"c": code_id}
    ).scalar_one()


def set_trial(conn: Connection, value: Any) -> None:
    conn.execute(
        text(
            "INSERT INTO app_settings (key, value) VALUES ('trial', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"v": json.dumps(value)},
    )


def grant(conn: Connection, owner: UUID, plan: str | None, **columns: Any) -> None:
    """An admin grant (as ``classbeat_maint`` would write it)."""
    values: dict[str, Any] = {"owner_id": owner, "plan_id": plan, "source": "grant", **columns}
    names = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(text(f"INSERT INTO entitlements ({names}) VALUES ({params})"), values)  # noqa: S608


def subscription_event(
    conn: Connection, owner: UUID, plan: str, period_start: datetime, period_end: datetime,
    *, kind: str = "started", event_id: str | None = None,
) -> UUID:  # fmt: skip
    """A `billing_events` row as the writer would insert it (`classbeat_maint`; Dummy provider)."""
    values = {
        "o": owner, "e": event_id or f"dummy_{uuid4().hex}", "k": kind, "p": plan,
        "s": period_start, "end": period_end,
    }  # fmt: skip
    return conn.execute(
        text(
            "INSERT INTO billing_events (owner_id, provider, event_id, kind, plan_id, "
            "period_start, period_end, amount_minor, currency, outcome) VALUES "
            "(:o, 'dummy', :e, :k, :p, :s, :end, 0, 'USD', 'applied') RETURNING id"
        ),
        values,
    ).scalar_one()


def subscription(
    conn: Connection, owner: UUID, plan: str, *, starts_at: datetime, ends_at: datetime
) -> None:
    """A subscription entitlement with the event the CHECK demands (at most 32 days)."""
    event = subscription_event(conn, owner, plan, starts_at, ends_at)
    grant(
        conn, owner, plan, source="subscription", starts_at=starts_at, ends_at=ends_at,
        billing_event_id=event,
    )  # fmt: skip


def about(moment: str | datetime, days: float, slack: float = 120) -> bool:
    """Whether ``moment`` is ``days`` from now, within ``slack`` seconds."""
    when = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    expected = datetime.now(UTC) + timedelta(days=days)
    return abs((when - expected).total_seconds()) <= slack
