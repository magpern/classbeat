"""``classes.interpretation`` (ADR 0029 section 5; MA1b-3, #881)

Revision ID: 0044_class_interpretation
Revises: 0043_retire_soundless_flag
Create Date: 2026-11-10

How an ``interpret_request`` job read the instructor's request, before any design: the resolved
brief and music, the interpretation items and the design hand-off's stored refusal
(``classbeat.design.interpretation_row``). NULL for every class that has none. The CHECK bounds it
at 32768 bytes; the application checks the same measure first. No new table and no new policy:
``classes`` already has forced owner row security, and the application role already holds
table-level ``UPDATE`` on it, so the column needs no grant of its own. It goes with the class and
the account like the brief (``classbeat.db.retention``).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0044_class_interpretation"
down_revision: str | Sequence[str] | None = "0043_retire_soundless_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.classes ADD COLUMN interpretation jsonb "
        "CONSTRAINT classes_interpretation CHECK (octet_length(interpretation::text) <= 32768)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.classes DROP COLUMN interpretation")
