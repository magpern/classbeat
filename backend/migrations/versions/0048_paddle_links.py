"""Paddle on fakes: the provider CHECKs, ``billing_links`` and the webhook's refusals (#973,
ADR 0032 sections 3 and 5)

Revision ID: 0048_paddle_links
Revises: 0047_invite_requests
Create Date: 2026-11-12

- ``billing_events.provider`` and ``subscriptions.provider`` take ``fake_paddle`` and ``paddle``
  besides ``dummy``; events from the fake are stored as ``fake_paddle``, never as ``paddle``.
- ``billing_log.action`` takes ``webhook`` (``classbeat.admin.db.webhook_session``).
- ``billing_links`` (owned, forced row security, cascades with the account): the link a checkout
  creates before the buyer pays. A notification resolves its user only through it. Unique on
  ``(provider, checkout_ref)`` and ``(provider, subscription_ref)`` (global on purpose: a Paddle
  id selects one link whoever asks, the RLS guard lists both). ``customer_ref`` is **not** unique
  per row: Paddle keeps one customer per buyer, so a returning buyer's second checkout has a
  second link with the same customer id; the webhook refuses a customer id that another user's
  link holds (``customer_conflict``), so a customer id maps to at most one user.
  ``activated_at`` is when the link's first payment applied (the newest is the owner's current
  Paddle subscription); ``changed_at`` is when its scheduled change last changed (Paddle's
  ``occurred_at``, or classbeat's own change time), so an older ``subscription.updated``
  delivered later is ``stale_change``.
- ``billing_webhook_refusals`` (a system table: a refused notification may name no user): one row
  per refused ``(provider, event_id)``, so Paddle's retries of one event count once. Only a
  signature-verified notification reaches it.

Privileges: the app role reads ``billing_links`` (the export) and nothing of the refusals;
``classbeat_maint`` is granted in ``docker/postgres/maint/30-maint.sql``;
``docker/postgres/init/10-roles.sql`` re-grants the app's on a re-run.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0048_paddle_links"
down_revision: str | Sequence[str] | None = "0047_invite_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REF = "{0} ~ '^[a-z]{{2,5}}_[a-z0-9]{{1,64}}$'"  # a Paddle id, as `paddle_payload.ident` reads it

BILLING_LINKS = f"""
    CREATE TABLE public.billing_links (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        provider text NOT NULL
            CONSTRAINT billing_links_provider CHECK (provider IN ('fake_paddle', 'paddle')),
        checkout_ref text NOT NULL CONSTRAINT billing_links_checkout_ref
            CHECK ({REF.format("checkout_ref")}),
        customer_ref text CONSTRAINT billing_links_customer_ref
            CHECK ({REF.format("customer_ref")}),
        subscription_ref text CONSTRAINT billing_links_subscription_ref
            CHECK ({REF.format("subscription_ref")}),
        activated_at timestamptz,
        changed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT billing_links_active CHECK (activated_at IS NULL OR subscription_ref IS NOT NULL),
        CONSTRAINT billing_links_checkout_key UNIQUE (provider, checkout_ref),
        CONSTRAINT billing_links_subscription_key UNIQUE (provider, subscription_ref),
        CONSTRAINT billing_links_owner_id_id_key UNIQUE (owner_id, id)
    )"""

REFUSALS = """
    CREATE TABLE public.billing_webhook_refusals (
        provider text NOT NULL CONSTRAINT billing_webhook_refusals_provider
            CHECK (provider IN ('fake_paddle', 'paddle')),
        event_id text NOT NULL CONSTRAINT billing_webhook_refusals_event_id
            CHECK (event_id ~ '^evt_[A-Za-z0-9]{1,64}$'),
        reason text NOT NULL CONSTRAINT billing_webhook_refusals_reason
            CHECK (reason ~ '^[a-z_]{1,40}$'),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (provider, event_id)
    )"""

PROVIDERS = ("billing_events", "subscriptions")


def _provider_check(table: str, values: str) -> tuple[str, str]:
    return (
        f"ALTER TABLE public.{table} DROP CONSTRAINT {table}_provider",
        f"ALTER TABLE public.{table} ADD CONSTRAINT {table}_provider CHECK (provider IN ({values}))",
    )


def _action_check(values: str) -> tuple[str, str]:
    return (
        "ALTER TABLE public.billing_log DROP CONSTRAINT billing_log_action",
        f"ALTER TABLE public.billing_log ADD CONSTRAINT billing_log_action "
        f"CHECK (action IN ({values}))",
    )


def upgrade() -> None:
    for table in PROVIDERS:
        for statement in _provider_check(table, "'dummy', 'fake_paddle', 'paddle'"):
            op.execute(statement)
    for statement in _action_check("'checkout', 'change', 'prices', 'webhook'"):
        op.execute(statement)
    op.execute(BILLING_LINKS)
    enable_owner_rls("billing_links")
    op.execute("REVOKE ALL ON TABLE public.billing_links FROM classbeat_app")
    op.execute("GRANT SELECT ON TABLE public.billing_links TO classbeat_app")
    op.execute(REFUSALS)
    op.execute("REVOKE ALL ON TABLE public.billing_webhook_refusals FROM classbeat_app")


def downgrade() -> None:
    op.execute("DROP TABLE public.billing_webhook_refusals")
    op.execute("DROP TABLE public.billing_links")
    # The older schema knows only Dummy and the three user actions: drop what it cannot hold
    # (an event of a Paddle provider goes with the entitlement rows that trace to it).
    for table in ("billing_log", "entitlements", "billing_events", "subscriptions"):
        op.execute(f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM public.billing_log WHERE action = 'webhook'")
    op.execute(
        "DELETE FROM public.entitlements e USING public.billing_events b "
        "WHERE e.owner_id = b.owner_id AND e.billing_event_id = b.id AND b.provider <> 'dummy'"
    )
    op.execute("DELETE FROM public.billing_events WHERE provider <> 'dummy'")
    op.execute("DELETE FROM public.subscriptions WHERE provider <> 'dummy'")
    for table in ("billing_log", "entitlements", "billing_events", "subscriptions"):
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    for statement in _action_check("'checkout', 'change', 'prices'"):
        op.execute(statement)
    for table in PROVIDERS:
        for statement in _provider_check(table, "'dummy'"):
            op.execute(statement)
