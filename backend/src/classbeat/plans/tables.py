"""The M5 tables (ADR 0011 section 1), as SQLAlchemy Core definitions.

Migration ``0007_tiers`` creates them with their checks, row security, policies and triggers;
this is the query-side mirror (``tests/test_plans_schema.py`` keeps the two in step).

System tables: ``plans``, ``redeem_codes``, ``app_settings``, ``admin_audit``. Owned tables
(``owner_id``, RLS): ``entitlements``, ``code_redemptions``, ``credit_ledger``, ``quota_periods``.
"""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

metadata = MetaData()

# ADR 0011 section 7: the feature codes of M5. ADR 0031 section 6: ``billing_test`` is granted by
# an admin only (``GRANT_FEATURES``), never by a redeem code, a plan or a trial (``FEATURES``).
FEATURES = frozenset({"song_picking"})
GRANT_FEATURES = frozenset({"song_picking", "billing_test"})
SOURCES = ("grant", "code", "trial", "subscription")
LEDGER_STATES = ("reserved", "committed", "released", "refunded")
LEDGER_REASONS = ("failed", "cancelled", "expired", "admin_refund")
SETTING_KEYS = (
    "signup_mode",
    "song_picking",
    "swedebeat_enabled",
    "running_enabled",
    "trial",
    "banner",
    "mail_sender_name",
    "mail_support_email",
    "mail_footer",
    "mail_kinds_disabled",
)
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
    # Migration 0026 (#496): the class length cap, the feature gates and the prices. The prices
    # are readable by classbeat_maint only (migration 0030, #510): select(plans) as the app fails.
    Column("max_class_minutes", Integer, nullable=False, server_default=text("180")),
    Column("games_allowed", Boolean, nullable=False, server_default=text("true")),
    Column("races_allowed", Boolean, nullable=False, server_default=text("true")),
    Column("ftp_tests_allowed", Boolean, nullable=False, server_default=text("true")),
    Column("monthly_price_cents", Integer),
    Column("intro_price_cents", Integer),
    Column("intro_price_until", Date),
    Column("currency", Text),
    # Migration 0042 (#806): per-plan Save/Update caps, NULL = the environment's setting.
    Column("saves_per_hour", Integer),
    Column("saves_per_day", Integer),
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
    # ADR 0033 (migration 0047): the invite whose tier this single-use code is; NULL for a code.
    _uuid("invite_id"),
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
    # Migration 0046 (ADR 0031 section 3): the event behind a ``subscription`` row.
    _uuid("billing_event_id"),
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

# Migration 0046 (ADR 0031 section 3): owned. The app role reads them for the export, without the
# price columns (``basis_minor``, ``basis_currency``, ``amount_minor``, ``currency``); the writer
# on the maint engine (``classbeat.billing.writer``) is the only code that writes them.
billing_events = Table(
    "billing_events",
    metadata,
    _id(),
    _owner(),
    Column("provider", Text, nullable=False),
    Column("event_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("plan_id", Text, ForeignKey("plans.id")),
    _ts("period_start", nullable=False),
    _ts("period_end", nullable=False),
    Column("amount_minor", BigInteger, nullable=False, server_default=text("0")),
    Column("currency", Text),
    Column("outcome", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
)

subscriptions = Table(
    "subscriptions",
    metadata,
    _id(),
    _owner(),
    Column("provider", Text, nullable=False),
    Column("plan_id", Text, ForeignKey("plans.id"), nullable=False),
    Column("anchor_day", SmallInteger, nullable=False),
    _ts("period_start", nullable=False),
    _ts("period_end", nullable=False),
    Column("basis_minor", BigInteger, nullable=False),
    Column("basis_currency", Text, nullable=False),
    Column("next_plan_id", Text, ForeignKey("plans.id")),
    Column("cancel_at_end", Boolean, nullable=False, server_default=text("false")),
    Column("status", Text, nullable=False, server_default="active"),
    _ts("last_event_at", nullable=False, default_now=True),
    _ts("created_at", nullable=False, default_now=True),
    _ts("updated_at", nullable=False, default_now=True),
)

billing_log = Table(
    "billing_log",
    metadata,
    _id(),
    _owner(),
    Column("action", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("reason", Text),
    Column("request_id", Text, nullable=False),
    _ts("created_at", nullable=False, default_now=True),
)
