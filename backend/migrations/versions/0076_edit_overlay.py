"""The edit overlay of a draft revision (ADR 0042 sections 3 and 4; #1391)

Revision ID: 0076_edit_overlay
Revises: 0075_drop_local_track_maps
Create Date: 2026-11-22

``class_revisions.edit_overlay`` (jsonb, nullable): the instructor's overrides, locks, anchors and
cut pins of a draft (``classbeat.design.edit_overlay_model``), **not** a field of ``content``, so
it is never sent to MOWL, never counted against the 64 KB ``content`` check, and the strict
``WorkoutDraft`` is unchanged. NULL on every existing row: no hash, approval or saved program
changes (``classbeat.design.revision_hash``). A fully cleared overlay is stored as NULL, so the
check refuses an empty ``records`` list. The application bounds it to 200 records and 32 KB of
canonical JSON; the check is the same count and a byte bound on jsonb's own text form, which
spaces its separators (a wider bound, so the application's is the one that answers).

The downgrade drops the column (an overlay is the user's own edit record; the content it edited
stays in ``content``).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0076_edit_overlay"
down_revision: str | Sequence[str] | None = "0075_drop_local_track_maps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_RECORDS = 200  # ``edit_overlay_model.MAX_RECORDS``
MAX_TEXT_BYTES = 49152  # jsonb's text of a 32 KB canonical overlay, with its spaced separators


def upgrade() -> None:
    op.execute("ALTER TABLE public.class_revisions ADD COLUMN edit_overlay jsonb")
    op.execute(
        "ALTER TABLE public.class_revisions ADD CONSTRAINT class_revisions_edit_overlay CHECK ("
        "edit_overlay IS NULL OR ("
        "jsonb_typeof(edit_overlay) = 'object' "
        f"AND octet_length(edit_overlay::text) <= {MAX_TEXT_BYTES} "
        "AND CASE WHEN jsonb_typeof(edit_overlay -> 'records') = 'array' "
        f"THEN jsonb_array_length(edit_overlay -> 'records') BETWEEN 1 AND {MAX_RECORDS} "
        "ELSE false END))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.class_revisions DROP COLUMN edit_overlay")
