"""Per-plan class length, feature gates and prices (issue #496, ADR 0011 amendment 2026-11-03)

Revision ID: 0026_plan_gates
Revises: 0025_running_flag
Create Date: 2026-11-03

``plans`` (a system table, no ``owner_id``) gains, beside the four limits of migration 0007:

- ``max_class_minutes`` (10 to 180, the brief's own range): the longest class a plan designs;
- ``games_allowed``, ``races_allowed``, ``ftp_tests_allowed``: the feature gates;
- ``monthly_price_cents``, ``intro_price_cents``, ``intro_price_until``, ``currency``: prices,
  stored and edited in the admin area only while billing is off (M7). An introduction price comes
  with its end date and a price with its currency.

Seeded with the owner's placeholders (2026-11-03): Free 30 minutes without games, Instructor 60,
Pro 90; races and FTP tests allowed everywhere; no price. A plan added later gets the column
defaults (180 minutes, everything allowed, no price) unless its migration says otherwise.

The app role keeps its table-level ``SELECT`` on ``plans`` (``10-roles.sql``); the code never reads
a price outside ``classbeat.admin``. ``classbeat_maint``'s column grants are in
``docker/postgres/maint/30-maint.sql`` (guard 11).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026_plan_gates"
down_revision: str | Sequence[str] | None = "0025_running_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_PRICE_CENTS = 10_000_000  # 100 000.00 in any currency: a typo guard, not a business rule

COLUMNS = f"""
    ALTER TABLE public.plans
        ADD COLUMN max_class_minutes integer NOT NULL DEFAULT 180
            CONSTRAINT plans_max_class_minutes CHECK (max_class_minutes BETWEEN 10 AND 180),
        ADD COLUMN games_allowed boolean NOT NULL DEFAULT true,
        ADD COLUMN races_allowed boolean NOT NULL DEFAULT true,
        ADD COLUMN ftp_tests_allowed boolean NOT NULL DEFAULT true,
        ADD COLUMN monthly_price_cents integer CONSTRAINT plans_monthly_price_cents
            CHECK (monthly_price_cents BETWEEN 0 AND {MAX_PRICE_CENTS}),
        ADD COLUMN intro_price_cents integer CONSTRAINT plans_intro_price_cents
            CHECK (intro_price_cents BETWEEN 0 AND {MAX_PRICE_CENTS}),
        ADD COLUMN intro_price_until date CONSTRAINT plans_intro_price_until
            CHECK (intro_price_until BETWEEN DATE '2020-01-01' AND DATE '2100-12-31'),
        ADD COLUMN currency text CONSTRAINT plans_currency CHECK (currency ~ '^[A-Z]{{3}}$'),
        ADD CONSTRAINT plans_intro_price_period
            CHECK ((intro_price_cents IS NULL) = (intro_price_until IS NULL)),
        ADD CONSTRAINT plans_price_currency
            CHECK ((monthly_price_cents IS NULL AND intro_price_cents IS NULL)
                   OR currency IS NOT NULL)"""

# The owner's placeholders (issue #496); the admin edits them.
SEED = """
    UPDATE public.plans SET max_class_minutes = v.minutes, games_allowed = v.games
    FROM (VALUES ('free', 30, false), ('instructor', 60, true), ('pro', 90, true))
        AS v (id, minutes, games)
    WHERE plans.id = v.id"""

DROP = """
    ALTER TABLE public.plans
        DROP CONSTRAINT plans_price_currency,
        DROP CONSTRAINT plans_intro_price_period,
        DROP COLUMN currency,
        DROP COLUMN intro_price_until,
        DROP COLUMN intro_price_cents,
        DROP COLUMN monthly_price_cents,
        DROP COLUMN ftp_tests_allowed,
        DROP COLUMN races_allowed,
        DROP COLUMN games_allowed,
        DROP COLUMN max_class_minutes"""


def upgrade() -> None:
    op.execute(COLUMNS)
    op.execute(SEED)


def downgrade() -> None:
    op.execute(DROP)
