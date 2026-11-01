"""The M5 tables (ADR 0011 section 1), as SQLAlchemy Core definitions.

Migration ``0007_tiers`` creates them with their checks, row security, policies and triggers;
this is the query-side mirror (``tests/test_plans_schema.py`` keeps the two in step).

System tables: ``plans``, ``redeem_codes``, ``app_settings``, ``admin_audit``. Owned tables
(``owner_id``, RLS): ``entitlements``, ``code_redemptions``, ``credit_ledger``, ``quota_periods``.
"""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = MetaData()

# ADR 0011 section 7: the feature codes of M5.
FEATURES = frozenset({"song_picking"})
SOURCES = ("grant", "code", "trial", "subscription")
LEDGER_STATES = ("reserved", "committed", "released", "refunded")
LEDGER_REASONS = ("failed", "cancelled", "expired", "admin_refund")
SETTING_KEYS = ("signup_mode", "song_picking", "swedebeat_enabled", "trial", "banner")
AUDIT_OUTCOMES = ("ok", "refused", "error")
FREE = "free"


def _id() -> Column[Any]:
    return Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> Column[Any]:
    return Column(
        name,
        DateTime(timezone=True),
        nullable=nullable,
        server_default=text("now()") if default_now else None,
    )


def _owner() -> Column[Any]:
    return Column("owner_id", UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)


def _features() -> Column[Any]:
    return Column("features", ARRAY(Text), nullable=False, server_default=text("'{}'"))


def _uuid(name: str) -> Column[Any]:
    return Column(name, UUID(as_uuid=True))


plans = Table(
    "plans",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("rank", Integer, nullable=False),
    Column("classes_per_month", Integer, nullable=False),
    Column("revisions_per_class", Integer, nullable=False),
    Column("concurrent_jobs", Integer, nullable=False),
    Column("monthly_cost_micros", BigInteger, nullable=False),
    Column("route_key", Text, nullable=False, server_default="default"),
    _features(),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at", nullable=False, default_now=True),
    _uuid("updated_by"),
)

redeem_codes = Table(
    "redeem_codes",
    metadata,
    _id(),
    Column("code_hash", LargeBinary, nullable=False),
    Column("label", Text, nullable=False),
    Column("plan_id", Text, ForeignKey("plans.id")),
    _features(),
    Column("grant_days", Integer, nullable=False),
    Column("max_redemptions", Integer, nullable=False),
    Column("redeemed_count", Integer, nullable=False, server_default=text("0")),
    _ts("expires_at", nullable=False),
    _ts("disabled_at"),
    _uuid("created_by"),
    _ts("created_at", nullable=False, default_now=True),
)

entitlements = Table(
    "entitlements",
    metadata,
    _id(),
    _owner(),
    Column("plan_id", Text, ForeignKey("plans.id")),
    _features(),
    Column("source", Text, nullable=False),
    _ts("starts_at", nullable=False, default_now=True),
    _ts("ends_at"),
    Column("code_id", UUID(as_uuid=True), ForeignKey("redeem_codes.id")),
    _uuid("granted_by"),
    Column("note", Text),
    _ts("revoked_at"),
    _uuid("revoked_by"),
    _ts("created_at", nullable=False, default_now=True),
)

code_redemptions = Table(
    "code_redemptions",
    metadata,
    _id(),
    _owner(),
    Column("code_id", UUID(as_uuid=True), ForeignKey("redeem_codes.id"), nullable=False),
    Column("entitlement_id", UUID(as_uuid=True), nullable=False),
    _ts("created_at", nullable=False, default_now=True),
)

credit_ledger = Table(
    "credit_ledger",
    metadata,
    _id(),
    _owner(),
    _uuid("job_id"),
    Column("period", Date, nullable=False),
    Column("plan_id", Text, ForeignKey("plans.id"), nullable=False),
    Column("state", Text, nullable=False),
    Column("reason", Text),
    _ts("settled_at"),
    _uuid("refunded_by"),
    _ts("created_at", nullable=False, default_now=True),
)

quota_periods = Table(
    "quota_periods",
    metadata,
    _id(),
    _owner(),
    Column("period", Date, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
)

app_settings = Table(
    "app_settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", JSONB, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at", nullable=False, default_now=True),
    _uuid("updated_by"),
)

admin_audit = Table(
    "admin_audit",
    metadata,
    _id(),
    _ts("created_at", nullable=False, default_now=True),
    _uuid("actor_id"),
    Column("actor_email", Text, nullable=False),
    Column("action", Text, nullable=False),
    _uuid("target_user_id"),
    Column("target_kind", Text),
    Column("target_id", Text),
    Column("params", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("outcome", Text, nullable=False),
    Column("reason", Text),
    Column("request_id", Text, nullable=False),
    Column("ip_hash", LargeBinary, nullable=False),
)
