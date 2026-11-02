"""The approved Swedebeat match of a class (ADR 0016 section 6, B3)

Revision ID: 0022_swedebeat_approval
Revises: 0021_swedebeat_match
Create Date: 2026-11-02

``classes.approved_swedebeat_match_id`` is the ``swedebeat_match`` revision the user approved as
the selection the mix will be built from. B2 and B4 read it. Only the approval route sets it;
``revise_songs`` clears it when a song change becomes the current draft, because the approval is
of one list. The revision is one of the class's own (the same key as ``current_revision_id``).
No new table and no new policy: ``classes`` already has forced owner row security, and the
application role already holds table-level ``UPDATE`` on it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_swedebeat_approval"
down_revision: str | Sequence[str] | None = "0021_swedebeat_match"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.classes ADD COLUMN approved_swedebeat_match_id uuid")
    op.execute(
        "ALTER TABLE public.classes ADD CONSTRAINT classes_approved_swedebeat_match_fkey "
        "FOREIGN KEY (owner_id, id, approved_swedebeat_match_id) "
        "REFERENCES public.class_revisions (owner_id, class_id, id) "
        "ON DELETE SET NULL (approved_swedebeat_match_id)"
    )
    op.execute(
        "CREATE INDEX classes_owner_approved_match "
        "ON public.classes (owner_id, approved_swedebeat_match_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX public.classes_owner_approved_match")
    op.execute("ALTER TABLE public.classes DROP CONSTRAINT classes_approved_swedebeat_match_fkey")
    op.execute("ALTER TABLE public.classes DROP COLUMN approved_swedebeat_match_id")
