"""``classes.dismissed_rules`` (ADR 0017 section 4; P1b, #393)

Revision ID: 0017_dismissed_rules
Revises: 0016_swedebeat_flag
Create Date: 2026-11-01

The soft-rule ids a user dismissed for one class: a JSON array of at most 32 strings (the CHECK holds that), empty by
default. Only ids from ``classbeat.design.rules.ids`` are written, by the class dismissal route;
the database holds the shape and the cap (the ids themselves are checked by the application, so
adding a rule id needs no migration). No new table and no new policy: ``classes`` already has
forced owner row security, and the application role already holds table-level ``UPDATE`` on it
(the pick writes ``current_revision_id``), so the column needs no grant of its own.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_dismissed_rules"
down_revision: str | Sequence[str] | None = "0016_swedebeat_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.classes ADD COLUMN dismissed_rules jsonb NOT NULL DEFAULT '[]' "
        "CONSTRAINT classes_dismissed_rules CHECK ("
        "jsonb_typeof(dismissed_rules) = 'array' AND jsonb_array_length(dismissed_rules) <= 32 "
        "AND NOT jsonb_path_exists(dismissed_rules, 'strict $[*] ? (@.type() != \"string\")'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.classes DROP COLUMN dismissed_rules")
