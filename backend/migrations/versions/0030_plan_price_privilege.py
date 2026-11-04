"""Plan prices by database privilege: the app role reads every column of ``plans`` but the prices
(issue #510, ADR 0011 amendment 2026-11-04)

Revision ID: 0030_plan_price_privilege
Revises: 0029_beat_one_corrections
Create Date: 2026-11-04

Migration 0026 added ``monthly_price_cents``, ``intro_price_cents``, ``intro_price_until`` and
``currency`` to ``plans`` and left the app role its table-level ``SELECT``; only a source scan kept
the prices out of the app, and a whole-row read (``SELECT p``, ``(p).*``, ``jsonb_agg(p)``) got
past it. Now the database refuses: ``classbeat_app`` loses the table-level ``SELECT`` and gets it
on the columns below, one by one. A read that names a price column, or reads the row whole, fails
with "permission denied" as the app role. ``classbeat_maint`` keeps the prices (its column grants
are in ``docker/postgres/maint/30-maint.sql``, guard 11) and is the only role besides the owner
that reads them.

A column added to ``plans`` later is not readable by the app until its migration grants it (a
column grant does not cover new columns); guard 2 (``tests/role_guard.py``) lists exactly these.
``docker/postgres/init/10-roles.sql`` grants the same list when it runs again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_plan_price_privilege"
down_revision: str | Sequence[str] | None = "0029_beat_one_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every column of ``plans`` at this revision except the prices and their currency. Frozen here:
# a later migration that adds a column grants it itself (or not).
APP_COLUMNS = (
    "id",
    "name",
    "rank",
    "classes_per_month",
    "revisions_per_class",
    "concurrent_jobs",
    "monthly_cost_micros",
    "route_key",
    "features",
    "created_at",
    "updated_at",
    "updated_by",
    "max_class_minutes",
    "games_allowed",
    "races_allowed",
    "ftp_tests_allowed",
)
PRICE_COLUMNS = ("monthly_price_cents", "intro_price_cents", "intro_price_until", "currency")
APP_GRANT = f"GRANT SELECT ({', '.join(APP_COLUMNS)}) ON TABLE public.plans TO classbeat_app"


def upgrade() -> None:
    # A table-level REVOKE also drops every column grant, so the result is exactly APP_GRANT.
    op.execute("REVOKE ALL ON TABLE public.plans FROM classbeat_app")
    op.execute(APP_GRANT)


def downgrade() -> None:
    op.execute("REVOKE ALL ON TABLE public.plans FROM classbeat_app")
    op.execute("GRANT SELECT ON TABLE public.plans TO classbeat_app")
