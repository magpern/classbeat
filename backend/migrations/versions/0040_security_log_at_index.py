"""Index ``security_log (at)`` for the admin abuse view (#750)

Revision ID: 0040_security_log_at_index
Revises: 0039_analysis_metrics
Create Date: 2026-11-07

``GET /api/admin/abuse`` (``classbeat.admin.abuse``, #749) reads the last 5 to 240 minutes of
``security_log`` across all users, as ``classbeat_maint``, under a 30 s statement timeout. The only
index that led with a time was ``(owner_id, at)``, which a query without an ``owner_id`` filter
cannot range-scan, so every call read the whole table (rows are capped per user, about 504,000 per
user at the defaults, so the table grows with the number of users). ``security_log_at`` makes the
window a range scan. The per-user purge (``owner_id = :o AND at < ...``) keeps using
``security_log_owner_at``. Costs one more index entry per logged request.

An ordinary (locking) ``CREATE INDEX``: the table is at most a week of rows and the migration runs
before the web process starts. No grant: an index needs none, and ``classbeat_maint``'s column grant
(``docker/postgres/maint/30-maint.sql``) is unchanged. The downgrade drops the index.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0040_security_log_at_index"
down_revision: str | Sequence[str] | None = "0039_analysis_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX security_log_at ON public.security_log (at)")


def downgrade() -> None:
    op.execute("DROP INDEX public.security_log_at")
