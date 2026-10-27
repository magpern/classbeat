"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No tables yet. Alembic's own bookkeeping table is closed to the app role.

    Default privileges gave ``classbeat_app`` DML on ``alembic_version`` when Alembic created
    it; the app must not be able to change migration state (ADR 0002, SYSTEM_TABLES).
    """
    op.execute("REVOKE ALL ON TABLE public.alembic_version FROM classbeat_app, PUBLIC")


def downgrade() -> None:
    """Nothing to undo."""
