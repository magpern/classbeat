"""M5 hardening: entitlement terms, ledger settles and redeem counts in the database (#271)

Revision ID: 0010_m5_hardening
Revises: 0009_models
Create Date: 2026-10-31

Defence in depth for what ``classbeat.plans`` and the settle already do (ADR 0011 sections 1 to
3, note of 2026-10-31), so SQL injected as ``classbeat_app`` can't do more than the app does:

- ``entitlements_app_sources`` (restrictive ``INSERT``): besides ``source IN ('code', 'trial')``,
  the row starts by now and ends within 366 days (``add_entitlement``'s 1 to 366 days), and a
  trial is a plan only, ranked at most ``instructor``. A missing plan or ``instructor`` refuses.
- ``credit_ledger_guard``: the app's settle sets ``reason`` NULL for ``committed`` and
  ``failed``, ``cancelled`` or ``expired`` for ``released``, with ``settled_at = now()``;
  ``admin_refund`` stays the maint refund's, which must also name the admin (``refunded_by``).
- ``redeem_codes_guard`` (new, ``BEFORE UPDATE``): the app may only add one use to a code that
  is neither disabled nor expired, up to ``max_redemptions``, and change nothing else; every
  other role (maint's ``disabled_at``, the ``SET NULL`` of ``created_by``) leaves the count alone.

Both trigger functions are ``SECURITY INVOKER`` (they decide by ``current_user``) and pinned in
``classbeat/db/queue_digests.py`` and ``docker/postgres/queue/20-queue.sql`` (ADR 0005 section 3,
amendment of 2026-10-31). ``CREATE OR REPLACE`` keeps the ledger function's owner, ACL and trigger.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_m5_hardening"
down_revision: str | Sequence[str] | None = "0009_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITLEMENT_POLICY = """
    CREATE POLICY entitlements_app_sources ON public.entitlements AS RESTRICTIVE FOR INSERT
        TO PUBLIC WITH CHECK (
            source IN ('code', 'trial')
            AND starts_at <= now()
            AND ends_at IS NOT NULL
            AND ends_at <= now() + make_interval(days => 366)
            AND (source <> 'trial' OR (
                code_id IS NULL AND cardinality(features) = 0
                AND (SELECT p.rank FROM public.plans p WHERE p.id = plan_id)
                    <= (SELECT p.rank FROM public.plans p WHERE p.id = 'instructor'))))"""

# 0007's policy, for the downgrade.
OLD_ENTITLEMENT_POLICY = (
    "CREATE POLICY entitlements_app_sources ON public.entitlements AS RESTRICTIVE FOR INSERT "
    "TO PUBLIC WITH CHECK (source IN ('code', 'trial'))"
)

LEDGER_GUARD = """
    CREATE OR REPLACE FUNCTION public.credit_ledger_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
    BEGIN
        -- The table owner only: the cascade of a deleted job (ON DELETE SET NULL (job_id)),
        -- which runs as the owner of credit_ledger.
        IF current_user = 'classbeat_owner' AND OLD.job_id IS NOT NULL AND NEW.job_id IS NULL
                AND to_jsonb(NEW) - 'job_id' = to_jsonb(OLD) - 'job_id' THEN
            RETURN NEW;
        END IF;
        -- The app role's settle: reserved -> committed (no reason) | released (how the job
        -- ended), stamped now().
        IF current_user = 'classbeat_app' AND OLD.state = 'reserved'
                AND ((NEW.state = 'committed' AND NEW.reason IS NULL)
                     OR (NEW.state = 'released'
                         AND NEW.reason IN ('failed', 'cancelled', 'expired')))
                AND NEW.settled_at = now()
                AND to_jsonb(NEW) - ARRAY['state', 'reason', 'settled_at']
                    = to_jsonb(OLD) - ARRAY['state', 'reason', 'settled_at'] THEN
            RETURN NEW;
        END IF;
        -- The admin refund: committed -> refunded, with its reason and the admin, never a live
        -- reservation.
        IF current_user = 'classbeat_maint' AND OLD.state = 'committed'
                AND NEW.state = 'refunded' AND NEW.reason = 'admin_refund'
                AND NEW.refunded_by IS NOT NULL
                AND to_jsonb(NEW) - ARRAY['state', 'reason', 'settled_at', 'refunded_by']
                    = to_jsonb(OLD) - ARRAY['state', 'reason', 'settled_at', 'refunded_by'] THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'credit_ledger: change refused for role %', current_user
            USING ERRCODE = 'insufficient_privilege';
    END
    $$"""

# 0008's body, byte for byte, for the downgrade (its digest is what 0008 pinned;
# tests/test_plans_hardening.py checks it equals 0008's ``LEDGER_GUARD``).
PREVIOUS_LEDGER_GUARD = """
    CREATE OR REPLACE FUNCTION public.credit_ledger_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
    BEGIN
        -- The table owner only: the cascade of a deleted job (ON DELETE SET NULL (job_id)),
        -- which runs as the owner of credit_ledger.
        IF current_user = 'classbeat_owner' AND OLD.job_id IS NOT NULL AND NEW.job_id IS NULL
                AND to_jsonb(NEW) - 'job_id' = to_jsonb(OLD) - 'job_id' THEN
            RETURN NEW;
        END IF;
        -- The app role's settle: reserved -> committed | released.
        IF current_user = 'classbeat_app' AND OLD.state = 'reserved'
                AND NEW.state IN ('committed', 'released')
                AND to_jsonb(NEW) - ARRAY['state', 'reason', 'settled_at']
                    = to_jsonb(OLD) - ARRAY['state', 'reason', 'settled_at'] THEN
            RETURN NEW;
        END IF;
        -- The admin refund: committed -> refunded, never a live reservation.
        IF current_user = 'classbeat_maint' AND OLD.state = 'committed'
                AND NEW.state = 'refunded'
                AND to_jsonb(NEW) - ARRAY['state', 'reason', 'settled_at', 'refunded_by']
                    = to_jsonb(OLD) - ARRAY['state', 'reason', 'settled_at', 'refunded_by'] THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'credit_ledger: change refused for role %', current_user
            USING ERRCODE = 'insufficient_privilege';
    END
    $$"""

REDEEM_GUARD = """
    CREATE FUNCTION public.redeem_codes_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
    BEGIN
        -- The app role's redeem: one more use of a live code, and nothing else.
        IF current_user = 'classbeat_app' THEN
            IF NEW.redeemed_count = OLD.redeemed_count + 1
                    AND NEW.redeemed_count <= OLD.max_redemptions
                    AND OLD.disabled_at IS NULL AND OLD.expires_at > now()
                    AND to_jsonb(NEW) - 'redeemed_count' = to_jsonb(OLD) - 'redeemed_count' THEN
                RETURN NEW;
            END IF;
        -- Every other role (maint's disable, the SET NULL of a deleted creator): the count
        -- stays as it is.
        ELSIF NEW.redeemed_count = OLD.redeemed_count THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'redeem_codes: change refused for role %', current_user
            USING ERRCODE = 'insufficient_privilege';
    END
    $$"""

REDEEM_TRIGGER = (
    "CREATE TRIGGER redeem_codes_guard BEFORE UPDATE ON public.redeem_codes "
    "FOR EACH ROW EXECUTE FUNCTION public.redeem_codes_guard()"
)


def upgrade() -> None:
    op.execute("DROP POLICY entitlements_app_sources ON public.entitlements")
    op.execute(ENTITLEMENT_POLICY)
    op.execute(LEDGER_GUARD)
    # Executable by nobody but the owner (ADR 0002 section 4's default privileges); a trigger
    # function needs no EXECUTE to fire.
    op.execute(REDEEM_GUARD)
    op.execute(REDEEM_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER redeem_codes_guard ON public.redeem_codes")
    op.execute("DROP FUNCTION public.redeem_codes_guard()")
    op.execute(PREVIOUS_LEDGER_GUARD)
    op.execute("DROP POLICY entitlements_app_sources ON public.entitlements")
    op.execute(OLD_ENTITLEMENT_POLICY)
