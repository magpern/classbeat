"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-27
"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Empty on purpose: the schema starts with the milestones that need tables."""


def downgrade() -> None:
    """Nothing to undo."""
