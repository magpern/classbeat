"""The Swedebeat catalogue match revision kind (ADR 0016 §5, B1)

Revision ID: 0021_swedebeat_match
Revises: 0020_swedebeat_analyses
Create Date: 2026-11-02

- ``class_revisions.kind`` allows ``swedebeat_match``: the immutable result of matching one
  ``song_list`` revision of the class against Swedebeat's catalogue (``classbeat.swedebeat_match``).
  Its content is Spotify-derived (the songs' titles and artists) and Swedebeat's (the catalogue's
  titles, artists and lengths); it goes with the class and the account, like the list it matches
  (the existing cascades; the retention registry names the kind).
- ``feature_off`` is already a job category (migration 0020). A queued match ends so, calling
  nothing, while the effective ``swedebeat_enabled`` flag is off.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_swedebeat_match"
down_revision: str | Sequence[str] | None = "0020_swedebeat_analyses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


OLD_REVISION_KINDS = _quoted("draft song_list")
REVISION_KINDS = _quoted("draft song_list swedebeat_match")


def _replace(expression: str) -> None:
    op.execute("ALTER TABLE public.class_revisions DROP CONSTRAINT class_revisions_kind")
    op.execute(
        "ALTER TABLE public.class_revisions ADD CONSTRAINT class_revisions_kind "
        f"CHECK ({expression})"
    )


def upgrade() -> None:
    _replace(f"kind IN ({REVISION_KINDS})")


def downgrade() -> None:
    # The owner is exempt from row security while it is not forced (as in 0011 and 0020). A match
    # revision is a leaf: nothing refers to it but a later revision's ``parent_id`` (SET NULL).
    # ``revisions_immutable`` forbids UPDATE, not DELETE.
    op.execute("ALTER TABLE public.class_revisions NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM public.class_revisions WHERE kind = 'swedebeat_match'")
    op.execute("ALTER TABLE public.class_revisions FORCE ROW LEVEL SECURITY")
    _replace(f"kind IN ({OLD_REVISION_KINDS})")
