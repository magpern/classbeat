"""Subscriptions, billing events, the billing log and the paid entitlement (issue #948, ADR 0031)

Revision ID: 0046_billing
Revises: 0045_plan_names_and_numbers
Create Date: 2026-11-11

Three owned tables (forced row security, cascade with the account):

- ``subscriptions``: one row per user (``UNIQUE (owner_id)``): provider, plan, ``anchor_day``
  (1 to 31), the period, the basis (the full monthly price charged for it), a scheduled downgrade
  *or* a cancel (never both: ``subscriptions_one_change``) and the status.
- ``billing_events``: every applied or ignored provider event. ``UNIQUE (provider, event_id)`` is
  global on purpose (a replay under any owner is a no-op; the one listed exception of the RLS
  guard's owner-inclusive key check). ``amount_minor >= 0``: no refund event exists.
- ``billing_log``: one row per user billing action (no IP, no amount).

``entitlements`` gains ``billing_event_id`` (owner-inclusive foreign key, ``NO ACTION``, so the
account's cascade to both tables is checked at the end of the statement) and the CHECK that
every ``subscription`` row traces to one event, names a plan, carries no feature and lasts at
most 32 days; a unique index makes the trace one to one. The feature ``billing_test`` joins
``song_picking`` on ``entitlements`` only (``plans`` and ``redeem_codes`` keep their list), and
only as a grant of at most 90 days.

Privileges: the app role reads the three tables for the export but never a price
(``billing_events.amount_minor`` and ``currency``, ``subscriptions.basis_minor`` and
``basis_currency``: guard 2). ``classbeat_maint`` is granted in
``docker/postgres/maint/30-maint.sql``; ``docker/postgres/init/10-roles.sql`` re-grants the app
columns on a re-run.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0046_billing"
down_revision: str | Sequence[str] | None = "0045_plan_names_and_numbers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENCY = "{0} ~ '^[A-Z]{{3}}$'"

BILLING_EVENTS = f"""
    CREATE TABLE public.billing_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        provider text NOT NULL CONSTRAINT billing_events_provider CHECK (provider IN ('dummy')),
        event_id text NOT NULL
            CONSTRAINT billing_events_event_id CHECK (char_length(event_id) BETWEEN 1 AND 200),
        kind text NOT NULL CONSTRAINT billing_events_kind CHECK (
            kind IN ('started', 'renewed', 'upgraded', 'change_scheduled', 'ended')),
        plan_id text REFERENCES public.plans (id),
        period_start timestamptz NOT NULL,
        period_end timestamptz NOT NULL,
        amount_minor bigint NOT NULL DEFAULT 0
            CONSTRAINT billing_events_amount CHECK (amount_minor >= 0),
        currency text CONSTRAINT billing_events_currency CHECK ({CURRENCY.format("currency")}),
        outcome text NOT NULL CONSTRAINT billing_events_outcome
            CHECK (outcome IN ('applied', 'ignored')),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT billing_events_period CHECK (period_end > period_start),
        CONSTRAINT billing_events_paid CHECK (
            kind NOT IN ('started', 'renewed', 'upgraded')
            OR (plan_id IS NOT NULL AND currency IS NOT NULL)),
        CONSTRAINT billing_events_provider_event_id_key UNIQUE (provider, event_id),
        CONSTRAINT billing_events_owner_id_id_key UNIQUE (owner_id, id)
    )"""

SUBSCRIPTIONS = f"""
    CREATE TABLE public.subscriptions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        provider text NOT NULL CONSTRAINT subscriptions_provider CHECK (provider IN ('dummy')),
        plan_id text NOT NULL REFERENCES public.plans (id),
        anchor_day smallint NOT NULL
            CONSTRAINT subscriptions_anchor_day CHECK (anchor_day BETWEEN 1 AND 31),
        period_start timestamptz NOT NULL,
        period_end timestamptz NOT NULL,
        basis_minor bigint NOT NULL CONSTRAINT subscriptions_basis CHECK (basis_minor >= 0),
        basis_currency text NOT NULL
            CONSTRAINT subscriptions_basis_currency CHECK ({CURRENCY.format("basis_currency")}),
        next_plan_id text REFERENCES public.plans (id),
        cancel_at_end boolean NOT NULL DEFAULT false,
        status text NOT NULL DEFAULT 'active'
            CONSTRAINT subscriptions_status CHECK (status IN ('active', 'ended')),
        last_event_at timestamptz NOT NULL DEFAULT now(),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT subscriptions_period CHECK (period_end > period_start),
        CONSTRAINT subscriptions_one_change CHECK (next_plan_id IS NULL OR NOT cancel_at_end),
        CONSTRAINT subscriptions_owner_id_key UNIQUE (owner_id),
        CONSTRAINT subscriptions_owner_id_id_key UNIQUE (owner_id, id)
    )"""

BILLING_LOG = """
    CREATE TABLE public.billing_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        action text NOT NULL
            CONSTRAINT billing_log_action CHECK (action IN ('checkout', 'change', 'prices')),
        outcome text NOT NULL
            CONSTRAINT billing_log_outcome CHECK (outcome IN ('ok', 'refused', 'error')),
        reason text CONSTRAINT billing_log_reason CHECK (char_length(reason) BETWEEN 1 AND 80),
        request_id text NOT NULL
            CONSTRAINT billing_log_request_id CHECK (char_length(request_id) BETWEEN 1 AND 100),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT billing_log_owner_id_id_key UNIQUE (owner_id, id)
    )"""

TABLES = (
    ("billing_events", BILLING_EVENTS),
    ("subscriptions", SUBSCRIPTIONS),
    ("billing_log", BILLING_LOG),
)

# The app role's columns: every column but the prices (guard 2 lists the same; 10-roles.sql too).
APP_GRANTS = {
    "billing_events": "SELECT (id, owner_id, provider, event_id, kind, plan_id, period_start, "
    "period_end, outcome, created_at)",
    "subscriptions": "SELECT (id, owner_id, provider, plan_id, anchor_day, period_start, "
    "period_end, next_plan_id, cancel_at_end, status, last_event_at, created_at, updated_at)",
    "billing_log": "SELECT",
}

ENTITLEMENT_UP = (
    "ALTER TABLE public.entitlements ADD COLUMN billing_event_id uuid",
    "ALTER TABLE public.entitlements ADD CONSTRAINT entitlements_billing_event_fkey "
    "FOREIGN KEY (owner_id, billing_event_id) REFERENCES public.billing_events (owner_id, id)",
    "CREATE UNIQUE INDEX entitlements_billing_event ON public.entitlements "
    "(owner_id, billing_event_id)",
    "ALTER TABLE public.entitlements ADD CONSTRAINT entitlements_subscription CHECK ("
    "source <> 'subscription' OR (billing_event_id IS NOT NULL AND plan_id IS NOT NULL "
    "AND cardinality(features) = 0 AND ends_at IS NOT NULL "
    "AND ends_at <= starts_at + interval '32 days'))",
    "ALTER TABLE public.entitlements DROP CONSTRAINT entitlements_features",
    "ALTER TABLE public.entitlements ADD CONSTRAINT entitlements_features CHECK ("
    "features <@ '{song_picking,billing_test}'::text[] AND cardinality(features) <= 16)",
    "ALTER TABLE public.entitlements ADD CONSTRAINT entitlements_billing_test CHECK ("
    "NOT ('billing_test' = ANY (features)) OR (source = 'grant' AND ends_at IS NOT NULL "
    "AND ends_at <= starts_at + interval '90 days'))",
)
ENTITLEMENT_DOWN = (
    "ALTER TABLE public.entitlements DROP CONSTRAINT entitlements_billing_test",
    # The older schema has neither a subscription row nor the tester feature: drop them (a
    # grant left with no plan and no feature goes too).
    "ALTER TABLE public.entitlements NO FORCE ROW LEVEL SECURITY",
    "DELETE FROM public.entitlements WHERE source = 'subscription'",
    "DELETE FROM public.entitlements WHERE plan_id IS NULL AND features = '{billing_test}'",
    "UPDATE public.entitlements SET features = array_remove(features, 'billing_test') "
    "WHERE 'billing_test' = ANY (features)",
    "ALTER TABLE public.entitlements FORCE ROW LEVEL SECURITY",
    "ALTER TABLE public.entitlements DROP CONSTRAINT entitlements_features",
    "ALTER TABLE public.entitlements ADD CONSTRAINT entitlements_features CHECK ("
    "features <@ '{song_picking}'::text[] AND cardinality(features) <= 16)",
    "ALTER TABLE public.entitlements DROP CONSTRAINT entitlements_subscription",
    "DROP INDEX public.entitlements_billing_event",
    "ALTER TABLE public.entitlements DROP CONSTRAINT entitlements_billing_event_fkey",
    "ALTER TABLE public.entitlements DROP COLUMN billing_event_id",
)


def upgrade() -> None:
    for name, ddl in TABLES:
        op.execute(ddl)
        enable_owner_rls(name)
        op.execute(f"REVOKE ALL ON TABLE public.{name} FROM classbeat_app")
        op.execute(f"GRANT {APP_GRANTS[name]} ON TABLE public.{name} TO classbeat_app")
    for statement in ENTITLEMENT_UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in ENTITLEMENT_DOWN:
        op.execute(statement)
    for name, _ in reversed(TABLES):
        op.execute(f"DROP TABLE public.{name}")
