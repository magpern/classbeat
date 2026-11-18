"""Approve and save in one step: the receipt's ``save`` and the class's ``save_pending`` (ADR 0038
section 7; #1189)

Revision ID: 0061_composer_save
Revises: 0060_class_pending_messages
Create Date: 2026-11-18

- ``jobs.approval_save``: the approval of a composer's song list (a ``design_with_songs`` job in
  mode ``fill``, ``swap`` or ``change``) asked for the save as well (``save: true``). It is set by
  the approval, with ``approved_at``, and lives on the job row next to ``approval_revision_id``
  and ``approval_hash``: the **approval receipt** a resume reads. False for every other job.
- ``classes.save_pending``: ``{revision_id, content_hash, job_id}`` of the first real draft a
  composer's phase 2 stored when its approval had ``save: true`` and the fit kept the approved
  design; the browser then saves exactly that revision (``classbeat.saving.save_pending``). Ids
  and a hash only: the CHECK names the three keys and bounds it at 512 bytes. NULL for every other
  class. It counts only while the revision it names is the class's current **and** newest one,
  so any newer revision voids it in the transaction that makes it (no trigger: the queue check
  admits no other user function, ``classbeat.db.queue_check``).

No new table and no new policy: ``classes`` and ``jobs`` already have forced owner row security,
and the application role already holds table-level ``UPDATE`` on both, so the columns need no
grant of their own. They go with their class and the account.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0061_composer_save"
down_revision: str | Sequence[str] | None = "0060_class_pending_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPROVAL_SAVE = "ALTER TABLE public.jobs ADD COLUMN approval_save boolean NOT NULL DEFAULT false"
SAVE_PENDING = (
    "ALTER TABLE public.classes ADD COLUMN save_pending jsonb "
    "CONSTRAINT classes_save_pending CHECK (save_pending IS NULL OR ("
    "jsonb_typeof(save_pending) = 'object' "
    "AND save_pending ?& ARRAY['revision_id', 'content_hash', 'job_id'] "
    "AND octet_length(save_pending::text) <= 512))"
)


def upgrade() -> None:
    op.execute(APPROVAL_SAVE)
    op.execute(SAVE_PENDING)


def downgrade() -> None:
    op.execute("ALTER TABLE public.classes DROP COLUMN save_pending")
    op.execute("ALTER TABLE public.jobs DROP COLUMN approval_save")
