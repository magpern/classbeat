"""Migration 0009 (#271, ADR 0011 note of 2026-10-31): the database refuses, for SQL run as the app
role, an entitlement ``add_entitlement`` would not add, a ledger settle the settle would not write
and a redeem count other than one more use of a live code; maint's refund needs its reason and
admin."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from dbtools import migration_module
from jobtools import engine_for, new_job, new_user
from test_plans_schema import (
    MAINT,
    REFUSED,
    as_owner_of,
    code,
    insert_sql,
    ledger,
    row,
    update_as,
)

POLICY = "row-level security"


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
def app(app_database_url: str) -> Iterator[Engine]:
    with engine_for(app_database_url) as engine:
        yield engine


def days(n: float) -> datetime:
    return datetime.now(UTC) + timedelta(days=n)


def insert_as(engine: Engine, user: uuid.UUID, table: str, values: dict[str, Any]) -> None:
    with as_owner_of(engine, user) as connection:
        connection.execute(insert_sql(table, values), values)


# --- entitlements_app_sources ---


@pytest.mark.parametrize(
    "values",
    [
        {"source": "trial", "plan_id": "pro", "ends_at": days(10)},  # ranks above instructor
        {"source": "trial", "plan_id": "instructor"},  # no end
        {"source": "trial", "plan_id": "instructor", "ends_at": days(367)},
        {"source": "trial", "plan_id": "instructor", "ends_at": days(10), "starts_at": days(1)},
        {"source": "trial", "plan_id": "free", "features": ["song_picking"], "ends_at": days(10)},
        {"source": "trial", "plan_id": None, "features": ["song_picking"], "ends_at": days(10)},
        {"source": "code", "plan_id": "pro", "ends_at": days(367), "code_id": "CODE"},
        {"source": "code", "plan_id": "pro", "code_id": "CODE"},  # no end
    ],
    ids=["trial-pro", "trial-open", "trial-367d", "trial-later", "trial-features",
         "trial-no-plan", "code-367d", "code-open"],
)  # fmt: skip
def test_the_app_cannot_add_an_entitlement_the_app_would_not(
    admin: Connection, app: Engine, values: dict[str, Any]
) -> None:
    user = new_user(admin)
    if values.get("code_id") == "CODE":
        values = {**values, "code_id": code(admin, plan_id="pro")}
    with pytest.raises(DBAPIError, match=POLICY):
        insert_as(app, user, "entitlements", {"owner_id": user, **values})
    assert admin.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0


def test_the_app_adds_a_code_or_trial_entitlement_within_the_terms(
    admin: Connection, app: Engine
) -> None:
    user = new_user(admin)
    with as_owner_of(app, user) as connection:
        for source, plan, code_id in (
            ("trial", "instructor", None),
            ("code", "pro", code(admin, plan_id="pro")),
        ):
            # As add_entitlement writes it, at the longest term (366 days).
            connection.execute(
                text(
                    "INSERT INTO entitlements (owner_id, plan_id, source, code_id, starts_at, "
                    "ends_at) VALUES (:o, :p, :s, :c, now(), now() + make_interval(days => 366))"
                ),
                {"o": user, "p": plan, "s": source, "c": code_id},
            )
    assert admin.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 2


# --- credit_ledger_guard ---

SETTLE = "UPDATE credit_ledger SET state = :s, reason = :r, settled_at = {at} WHERE id = :i"


@pytest.mark.parametrize(
    ("state", "reason", "at"),
    [
        ("committed", "failed", "now()"),
        ("committed", "admin_refund", "now()"),
        ("released", None, "now()"),
        ("released", "admin_refund", "now()"),
        ("released", "cancelled", "now() - interval '1 day'"),
        ("committed", None, "NULL"),
        ("committed", None, "'2026-01-01'"),
    ],
)
def test_the_app_settles_only_with_a_job_reason_and_now(
    admin: Connection, app: Engine, state: str, reason: str | None, at: str
) -> None:
    user = new_user(admin)
    reserved = ledger(admin, user, new_job(admin, user))
    with pytest.raises(DBAPIError, match=REFUSED):
        update_as(app, user, SETTLE.format(at=at), s=state, r=reason, i=reserved)
    assert row(admin, reserved).state == "reserved"


@pytest.mark.parametrize(
    ("state", "reason"),
    [("committed", None), ("released", "failed"), ("released", "cancelled"),
     ("released", "expired")],
)  # fmt: skip
def test_the_app_settles_a_job_as_the_settle_does(
    admin: Connection, app: Engine, state: str, reason: str | None
) -> None:
    user = new_user(admin)
    reserved = ledger(admin, user, new_job(admin, user))
    assert update_as(app, user, SETTLE.format(at="now()"), s=state, r=reason, i=reserved) == 1
    settled = row(admin, reserved)
    assert (settled.state, settled.reason, settled.settled_at is not None) == (state, reason, True)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE credit_ledger SET state = 'refunded', refunded_by = :by WHERE id = :i",
        "UPDATE credit_ledger SET state = 'refunded', reason = 'failed', refunded_by = :by "
        "WHERE id = :i",
        "UPDATE credit_ledger SET state = 'refunded', reason = 'admin_refund' WHERE id = :i",
    ],
    ids=["no-reason", "job-reason", "no-admin"],
)
def test_the_maint_refund_needs_its_reason_and_admin(admin: Connection, statement: str) -> None:
    user = new_user(admin)
    committed = ledger(
        admin, user, new_job(admin, user), state="committed", settled_at="2026-10-02"
    )
    admin.execute(text(f"SET ROLE {MAINT}"))
    try:
        with pytest.raises(DBAPIError, match=REFUSED):
            admin.execute(text(statement), {"i": committed, "by": user})
        admin.execute(
            text(
                "UPDATE credit_ledger SET state = 'refunded', reason = 'admin_refund', "
                "refunded_by = :by WHERE id = :i"
            ),
            {"i": committed, "by": user},
        )
    finally:
        admin.execute(text("RESET ROLE"))
    assert row(admin, committed).state == "refunded"


# --- redeem_codes_guard ---

COUNT = "UPDATE redeem_codes SET redeemed_count = {to} WHERE id = :i"


@pytest.mark.parametrize(
    ("setup", "to"),
    [
        ({"redeemed_count": 3}, "0"),  # a used code back to unused
        ({"redeemed_count": 3}, "2"),
        ({}, "redeemed_count + 2"),
        ({}, "redeemed_count"),
        ({"disabled_at": "now()"}, "redeemed_count + 1"),
        ({"expired": True}, "redeemed_count + 1"),
    ],
    ids=["reset", "down", "two", "same", "disabled", "expired"],
)
def test_the_app_only_adds_one_use_to_a_live_code(
    admin: Connection, app: Engine, setup: dict[str, Any], to: str
) -> None:
    user = new_user(admin)
    code_id = code(admin, max_redemptions=5, redeemed_count=setup.get("redeemed_count", 0))
    if "disabled_at" in setup:
        admin.execute(
            text("UPDATE redeem_codes SET disabled_at = now() WHERE id = :i"), {"i": code_id}
        )
    if setup.get("expired"):
        admin.execute(
            text("UPDATE redeem_codes SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": code_id},
        )
    before = admin.execute(
        text("SELECT redeemed_count FROM redeem_codes WHERE id = :i"), {"i": code_id}
    ).scalar_one()
    with pytest.raises(DBAPIError, match=REFUSED):
        update_as(app, user, COUNT.format(to=to), i=code_id)
    after = admin.execute(
        text("SELECT redeemed_count FROM redeem_codes WHERE id = :i"), {"i": code_id}
    ).scalar_one()
    assert after == before


def test_one_more_use_passes_and_other_roles_keep_the_count(admin: Connection, app: Engine) -> None:
    user = new_user(admin)
    code_id = code(admin, max_redemptions=2)
    assert update_as(app, user, COUNT.format(to="redeemed_count + 1"), i=code_id) == 1
    # Maint's disable and the owner's SET NULL of a creator leave the count alone ...
    admin.execute(text(f"SET ROLE {MAINT}"))
    try:
        admin.execute(
            text("UPDATE redeem_codes SET disabled_at = now() WHERE id = :i"), {"i": code_id}
        )
    finally:
        admin.execute(text("RESET ROLE"))
    # ... and no role but the app may change it, the superuser included.
    with pytest.raises(DBAPIError, match=REFUSED):
        admin.execute(text(COUNT.format(to="0")), {"i": code_id})
    assert (
        admin.execute(
            text("SELECT redeemed_count FROM redeem_codes WHERE id = :i"), {"i": code_id}
        ).scalar_one()
        == 1
    )


def test_the_downgrade_restores_0008s_ledger_guard() -> None:
    hardening = migration_module("m5_hardening")
    assert hardening.PREVIOUS_LEDGER_GUARD == migration_module("ledger_guard_owner").LEDGER_GUARD
