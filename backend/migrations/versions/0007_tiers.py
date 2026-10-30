"""Tiers, entitlements, codes, the credit ledger, settings and the admin audit (ADR 0011 s. 1)

Revision ID: 0007_tiers
Revises: 0006_spotify
Create Date: 2026-10-30

M5's only migration, issue DB1 (#207). It is "the next free revision": whichever of #207, #211
and #215 lands later re-chains its ``down_revision`` (and its file name) onto the head of main.

- System tables (``SYSTEM_TABLES``, no ``owner_id``): ``plans`` (seeded with ADR 0011 section 11
  Q1), ``redeem_codes``, ``app_settings`` and ``admin_audit``.
- Owned tables (ADR 0002 section 2, ``enable_owner_rls``): ``entitlements``, ``code_redemptions``,
  ``credit_ledger`` and ``quota_periods``.
- ``entitlements`` gets a restrictive ``INSERT`` policy (the app role adds only what redeem and
  trial add) and one trial per account, ever (a partial unique index).
- ``credit_ledger`` gets restrictive policies (insert only as ``reserved``, no delete, update only
  ``reserved -> committed | released``) and a ``BEFORE UPDATE`` trigger that decides by
  ``current_user``. Its function is ``SECURITY INVOKER``: a definer would make ``current_user`` the
  function's owner and refuse every settle. The cascade of a deleted job (``ON DELETE SET NULL
  (job_id)``) runs as the table owner and is allowed for every role.
- ``admin_audit`` is append-only: no privilege for the app role, and a trigger refuses ``UPDATE``
  and ``DELETE`` except the foreign keys' ``SET NULL`` of ``actor_id`` and ``target_user_id``.
- The app role's privileges are narrowed table by table to ADR 0011 section 1's list (the default
  privileges of ADR 0002 section 4 give it full DML on every new table).

``refunded_by`` and ``revoked_by`` carry no foreign key (ADR 0011 section 1 gives them none): a
``SET NULL`` from a deleted admin would be a ledger change the trigger refuses.

**Data migration** (ADR 0002 section 4 asks how it reaches owned rows): every job with
``credit_state = 'reserved'`` that has not ended gets a ``reserved`` ledger row (the UTC month of
its ``created_at``, plan ``free``), so it counts and settles like any other. It runs **per owner
with ``app.user_id`` set** (a ``DO`` block over ``users``), as an ``INSERT`` only: row security
stays forced and the trigger is not involved.

The queue functions (``docker/postgres/queue/20-queue.sql``) read no table this creates or changes,
so their version and digests stay.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0007_tiers"
down_revision: str | Sequence[str] | None = "0006_spotify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ADR 0011 section 7: the feature codes of M5 (``swedebeat`` and ``billing`` are flags shown off,
# not features anyone can hold).
FEATURES = "'{song_picking}'::text[]"
SETTING_KEYS = "'signup_mode', 'song_picking', 'trial', 'banner'"
SHA256 = "octet_length({0}) = 32"
FIRST_OF_MONTH = "{0} = CAST(date_trunc('month', {0}) AS date)"
OPEN_JOB_STATES = "'queued', 'running', 'waiting_for_approval'"


def _features(table: str) -> str:
    # ``<@`` is false for an array holding a NULL, so a NULL element is refused too.
    return (
        f"features text[] NOT NULL DEFAULT '{{}}' CONSTRAINT {table}_features "
        f"CHECK (features <@ {FEATURES} AND cardinality(features) <= 16)"
    )


PLANS = f"""
    CREATE TABLE public.plans (
        id text PRIMARY KEY CONSTRAINT plans_id CHECK (id ~ '^[a-z_]{{1,30}}$'),
        name text NOT NULL CONSTRAINT plans_name CHECK (char_length(name) BETWEEN 1 AND 50),
        rank integer NOT NULL CONSTRAINT plans_rank_key UNIQUE,
        classes_per_month integer NOT NULL
            CONSTRAINT plans_classes_per_month CHECK (classes_per_month BETWEEN 0 AND 10000),
        revisions_per_class integer NOT NULL
            CONSTRAINT plans_revisions_per_class CHECK (revisions_per_class BETWEEN 0 AND 200),
        concurrent_jobs integer NOT NULL
            CONSTRAINT plans_concurrent_jobs CHECK (concurrent_jobs BETWEEN 1 AND 16),
        monthly_cost_micros bigint NOT NULL CONSTRAINT plans_monthly_cost_micros
            CHECK (monthly_cost_micros BETWEEN 0 AND 10000000000),
        route_key text NOT NULL DEFAULT 'default'
            CONSTRAINT plans_route_key CHECK (route_key ~ '^[a-z0-9_]{{1,40}}$'),
        {_features("plans")},
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        updated_by uuid REFERENCES public.users (id) ON DELETE SET NULL
    )"""

# ADR 0011 section 11, Q1: Free 3 classes, 3 revisions, 1 job, $10; Instructor 30, 10, 2, $60;
# Pro 100, 25, 3, $200. No features (Q2: the owner grants ``song_picking`` to each tester).
PLAN_SEED = """
    INSERT INTO public.plans
        (id, name, rank, classes_per_month, revisions_per_class, concurrent_jobs,
         monthly_cost_micros)
    VALUES ('free', 'Free', 0, 3, 3, 1, 10000000),
           ('instructor', 'Instructor', 10, 30, 10, 2, 60000000),
           ('pro', 'Pro', 20, 100, 25, 3, 200000000)
    ON CONFLICT (id) DO NOTHING"""

REDEEM_CODES = f"""
    CREATE TABLE public.redeem_codes (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        code_hash bytea NOT NULL CONSTRAINT redeem_codes_code_hash_key UNIQUE
            CONSTRAINT redeem_codes_code_hash CHECK ({SHA256.format("code_hash")}),
        label text NOT NULL
            CONSTRAINT redeem_codes_label CHECK (char_length(label) BETWEEN 1 AND 80),
        plan_id text REFERENCES public.plans (id),
        {_features("redeem_codes")},
        grant_days integer NOT NULL
            CONSTRAINT redeem_codes_grant_days CHECK (grant_days BETWEEN 1 AND 366),
        max_redemptions integer NOT NULL
            CONSTRAINT redeem_codes_max_redemptions CHECK (max_redemptions BETWEEN 1 AND 1000),
        redeemed_count integer NOT NULL DEFAULT 0,
        expires_at timestamptz NOT NULL,
        disabled_at timestamptz,
        created_by uuid REFERENCES public.users (id) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT redeem_codes_redeemed_count
            CHECK (redeemed_count >= 0 AND redeemed_count <= max_redemptions),
        CONSTRAINT redeem_codes_grants_something
            CHECK (plan_id IS NOT NULL OR cardinality(features) > 0)
    )"""

ENTITLEMENTS = f"""
    CREATE TABLE public.entitlements (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        plan_id text REFERENCES public.plans (id),
        {_features("entitlements")},
        source text NOT NULL CONSTRAINT entitlements_source
            CHECK (source IN ('grant', 'code', 'trial', 'subscription')),
        starts_at timestamptz NOT NULL DEFAULT now(),
        ends_at timestamptz,
        code_id uuid REFERENCES public.redeem_codes (id) ON DELETE RESTRICT,
        granted_by uuid REFERENCES public.users (id) ON DELETE SET NULL (granted_by),
        note text CONSTRAINT entitlements_note CHECK (char_length(note) BETWEEN 1 AND 200),
        revoked_at timestamptz,
        revoked_by uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT entitlements_grants_something
            CHECK (plan_id IS NOT NULL OR cardinality(features) > 0),
        CONSTRAINT entitlements_period CHECK (ends_at > starts_at),
        CONSTRAINT entitlements_code CHECK (source <> 'code' OR code_id IS NOT NULL),
        CONSTRAINT entitlements_owner_id_id_key UNIQUE (owner_id, id)
    )"""

CODE_REDEMPTIONS = """
    CREATE TABLE public.code_redemptions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        code_id uuid NOT NULL REFERENCES public.redeem_codes (id) ON DELETE RESTRICT,
        entitlement_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT code_redemptions_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT code_redemptions_owner_id_code_id_key UNIQUE (owner_id, code_id),
        CONSTRAINT code_redemptions_owner_id_entitlement_id_key UNIQUE (owner_id, entitlement_id),
        CONSTRAINT code_redemptions_entitlement_fkey FOREIGN KEY (owner_id, entitlement_id)
            REFERENCES public.entitlements (owner_id, id) ON DELETE CASCADE
    )"""

CREDIT_LEDGER = f"""
    CREATE TABLE public.credit_ledger (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        job_id uuid,
        period date NOT NULL
            CONSTRAINT credit_ledger_period CHECK ({FIRST_OF_MONTH.format("period")}),
        plan_id text NOT NULL REFERENCES public.plans (id),
        state text NOT NULL CONSTRAINT credit_ledger_state
            CHECK (state IN ('reserved', 'committed', 'released', 'refunded')),
        reason text CONSTRAINT credit_ledger_reason
            CHECK (reason IN ('failed', 'cancelled', 'expired', 'admin_refund')),
        settled_at timestamptz,
        refunded_by uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT credit_ledger_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT credit_ledger_owner_id_job_id_key UNIQUE (owner_id, job_id),
        CONSTRAINT credit_ledger_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE SET NULL (job_id)
    )"""

QUOTA_PERIODS = f"""
    CREATE TABLE public.quota_periods (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        period date NOT NULL
            CONSTRAINT quota_periods_period CHECK ({FIRST_OF_MONTH.format("period")}),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT quota_periods_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT quota_periods_owner_id_period_key UNIQUE (owner_id, period)
    )"""

APP_SETTINGS = f"""
    CREATE TABLE public.app_settings (
        key text PRIMARY KEY CONSTRAINT app_settings_key CHECK (key IN ({SETTING_KEYS})),
        value jsonb NOT NULL
            CONSTRAINT app_settings_value CHECK (octet_length(value::text) <= 2048),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        updated_by uuid
    )"""

ADMIN_AUDIT = f"""
    CREATE TABLE public.admin_audit (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        created_at timestamptz NOT NULL DEFAULT now(),
        actor_id uuid REFERENCES public.users (id) ON DELETE SET NULL,
        actor_email text NOT NULL
            CONSTRAINT admin_audit_actor_email CHECK (char_length(actor_email) BETWEEN 1 AND 320),
        action text NOT NULL
            CONSTRAINT admin_audit_action CHECK (action ~ '^[a-z][a-z0-9_.]{{0,59}}$'),
        target_user_id uuid REFERENCES public.users (id) ON DELETE SET NULL,
        target_kind text
            CONSTRAINT admin_audit_target_kind CHECK (target_kind ~ '^[a-z][a-z0-9_]{{0,39}}$'),
        target_id text
            CONSTRAINT admin_audit_target_id CHECK (char_length(target_id) BETWEEN 1 AND 100),
        params jsonb NOT NULL DEFAULT '{{}}'::jsonb CONSTRAINT admin_audit_params
            CHECK (jsonb_typeof(params) = 'object' AND octet_length(params::text) <= 2048),
        outcome text NOT NULL
            CONSTRAINT admin_audit_outcome CHECK (outcome IN ('ok', 'refused', 'error')),
        reason text CONSTRAINT admin_audit_reason CHECK (reason ~ '^[a-z][a-z0-9_]{{0,59}}$'),
        request_id text NOT NULL
            CONSTRAINT admin_audit_request_id CHECK (char_length(request_id) BETWEEN 1 AND 100),
        ip_hash bytea NOT NULL CONSTRAINT admin_audit_ip_hash CHECK ({SHA256.format("ip_hash")})
    )"""

INDEXES = (
    # One trial per account, ever (ADR 0011 section 2): a second one is ``trial_used``.
    "CREATE UNIQUE INDEX entitlements_one_trial ON public.entitlements (owner_id) "
    "WHERE source = 'trial'",
    "CREATE INDEX entitlements_granted_by ON public.entitlements (granted_by)",
    "CREATE INDEX entitlements_code ON public.entitlements (code_id)",
    "CREATE INDEX code_redemptions_code ON public.code_redemptions (code_id)",
    # What a reservation counts (ADR 0011 section 3).
    "CREATE INDEX credit_ledger_owner_period ON public.credit_ledger (owner_id, period)",
    "CREATE INDEX admin_audit_created ON public.admin_audit (created_at)",
    "CREATE INDEX admin_audit_actor ON public.admin_audit (actor_id)",
    "CREATE INDEX admin_audit_target_user ON public.admin_audit (target_user_id)",
)

POLICIES = (
    # Grants come only through classbeat_maint (BYPASSRLS, ADR 0011 section 5).
    "CREATE POLICY entitlements_app_sources ON public.entitlements AS RESTRICTIVE FOR INSERT "
    "TO PUBLIC WITH CHECK (source IN ('code', 'trial'))",
    "CREATE POLICY credit_ledger_insert_reserved ON public.credit_ledger AS RESTRICTIVE "
    "FOR INSERT TO PUBLIC "
    "WITH CHECK (state = 'reserved' AND settled_at IS NULL AND refunded_by IS NULL)",
    # Only the account's cascade removes ledger rows (it runs as the owner, outside row security).
    "CREATE POLICY credit_ledger_no_delete ON public.credit_ledger AS RESTRICTIVE FOR DELETE "
    "TO PUBLIC USING (false)",
    "CREATE POLICY credit_ledger_settle ON public.credit_ledger AS RESTRICTIVE FOR UPDATE "
    "TO PUBLIC USING (state = 'reserved') WITH CHECK (state IN ('committed', 'released'))",
)

# ``to_jsonb(row) - columns`` compares every other column, including ones a later migration adds.
LEDGER_GUARD = """
    CREATE FUNCTION public.credit_ledger_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
    BEGIN
        -- Every role: the cascade of a deleted job (ON DELETE SET NULL (job_id)), which runs as
        -- the table owner.
        IF OLD.job_id IS NOT NULL AND NEW.job_id IS NULL
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

AUDIT_GUARD = """
    CREATE FUNCTION public.admin_audit_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
    BEGIN
        -- Only the foreign keys' SET NULL of a deleted actor or target.
        IF TG_OP = 'UPDATE'
                AND (NEW.actor_id IS NULL OR NEW.actor_id = OLD.actor_id)
                AND (NEW.target_user_id IS NULL OR NEW.target_user_id = OLD.target_user_id)
                AND to_jsonb(NEW) - ARRAY['actor_id', 'target_user_id']
                    = to_jsonb(OLD) - ARRAY['actor_id', 'target_user_id'] THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'admin_audit is append-only'
            USING ERRCODE = 'insufficient_privilege';
    END
    $$"""

TRIGGERS = (
    "CREATE TRIGGER credit_ledger_guard BEFORE UPDATE ON public.credit_ledger "
    "FOR EACH ROW EXECUTE FUNCTION public.credit_ledger_guard()",
    "CREATE TRIGGER admin_audit_guard BEFORE UPDATE OR DELETE ON public.admin_audit "
    "FOR EACH ROW EXECUTE FUNCTION public.admin_audit_guard()",
)

# ADR 0011 section 1: the app role's privileges on the new tables (guard 2 checks them).
APP_GRANTS = {
    "plans": "SELECT",
    "app_settings": "SELECT",
    "entitlements": "SELECT, INSERT",
    "code_redemptions": "SELECT, INSERT",
    "redeem_codes": "SELECT, UPDATE (redeemed_count)",
    "credit_ledger": "SELECT, INSERT, UPDATE",
    "quota_periods": "SELECT, INSERT, UPDATE",
    "admin_audit": "",
}

BACKFILL = f"""
    DO $$
    DECLARE
        uid uuid;
    BEGIN
        FOR uid IN SELECT id FROM public.users ORDER BY id LOOP
            PERFORM set_config('app.user_id', uid::text, true);
            INSERT INTO public.credit_ledger (owner_id, job_id, period, plan_id, state)
            SELECT j.owner_id, j.id,
                   CAST(date_trunc('month', j.created_at AT TIME ZONE 'UTC') AS date), 'free',
                   'reserved'
              FROM public.jobs j
             WHERE j.owner_id = uid AND j.credit_state = 'reserved'
               AND j.state IN ({OPEN_JOB_STATES})
            ON CONFLICT (owner_id, job_id) DO NOTHING;
        END LOOP;
        PERFORM set_config('app.user_id', '', true);
    END
    $$"""

TABLES = (
    ("plans", PLANS, False),
    ("redeem_codes", REDEEM_CODES, False),
    ("entitlements", ENTITLEMENTS, True),
    ("code_redemptions", CODE_REDEMPTIONS, True),
    ("credit_ledger", CREDIT_LEDGER, True),
    ("quota_periods", QUOTA_PERIODS, True),
    ("app_settings", APP_SETTINGS, False),
    ("admin_audit", ADMIN_AUDIT, False),
)


def upgrade() -> None:
    for name, ddl, owned in TABLES:
        op.execute(ddl)
        if owned:
            enable_owner_rls(name)
    op.execute(PLAN_SEED)
    for statement in (*INDEXES, *POLICIES, LEDGER_GUARD, AUDIT_GUARD, *TRIGGERS):
        op.execute(statement)
    # Functions made by the owner are executable by nobody else (ADR 0002 section 4's default
    # privileges); a trigger function needs no EXECUTE to fire.
    for table, privileges in APP_GRANTS.items():
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM classbeat_app")
        if privileges:
            op.execute(f"GRANT {privileges} ON TABLE public.{table} TO classbeat_app")
    op.execute(BACKFILL)


def downgrade() -> None:
    for name, _, _ in reversed(TABLES):
        op.execute(f"DROP TABLE public.{name}")
    op.execute("DROP FUNCTION public.admin_audit_guard()")
    op.execute("DROP FUNCTION public.credit_ledger_guard()")
