"""Per-plan Save and Update caps (issue #806, part of #632 item 5)

Revision ID: 0042_plan_save_caps
Revises: 0041_passkeys
Create Date: 2026-11-08

``plans`` gains ``saves_per_hour`` (1 to 1000) and ``saves_per_day`` (1 to 10 000), the same ranges
as ``CLASSBEAT_SAVES_PER_HOUR`` and ``CLASSBEAT_SAVES_PER_DAY``. Both are NULL on every plan: a plan
without a value uses the environment's setting, and a value can only tighten it (the effective cap
is the smaller of the two, ``classbeat.quota.save_caps_policy``).

Since migration 0030 the app role holds column-level ``SELECT`` on ``plans``, so the new columns
are granted here (and in ``docker/postgres/init/10-roles.sql``, which a re-run applies). Nothing
edits them in the admin area yet, so ``classbeat_maint`` gets no grant (guard 11).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_plan_save_caps"
down_revision: str | Sequence[str] | None = "0041_passkeys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = """
    ALTER TABLE public.plans
        ADD COLUMN saves_per_hour integer
            CONSTRAINT plans_saves_per_hour CHECK (saves_per_hour BETWEEN 1 AND 1000),
        ADD COLUMN saves_per_day integer
            CONSTRAINT plans_saves_per_day CHECK (saves_per_day BETWEEN 1 AND 10000)"""
GRANT = "GRANT SELECT (saves_per_hour, saves_per_day) ON TABLE public.plans TO classbeat_app"


def upgrade() -> None:
    op.execute(COLUMNS)
    op.execute(GRANT)


def downgrade() -> None:
    # Dropping a column drops its column privileges with it.
    op.execute("ALTER TABLE public.plans DROP COLUMN saves_per_day, DROP COLUMN saves_per_hour")
