"""swedebeat_track_maps: the local analyser is gone (ADR 0040 amendment; #1356)

Revision ID: 0075_drop_local_track_maps
Revises: 0074_drop_spotdown_shadow
Create Date: 2026-11-22

spotdown is the only analysis path (ADR 0040 amendment "spotdown is the only analysis path", items
3 and 6), so the ``local`` rows of ``swedebeat_track_maps`` (``mowl.music.analyze_track`` in the
sandboxed child) are deleted (a cache: nothing reads them any more), the ``analyser`` check admits
only ``spotdown`` and the column loses its ``local`` default (a row is stored with its analyser
and provenance, which the ``swedebeat_track_maps_provenance_spotdown`` check keeps together).

The downgrade restores the check (``local`` or ``spotdown``) and the default ``local``; the deleted
rows are not restored (the cache fills again).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0075_drop_local_track_maps"
down_revision: str | Sequence[str] | None = "0074_drop_spotdown_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "public.swedebeat_track_maps"
CHECK = "swedebeat_track_maps_analyser"


def upgrade() -> None:
    # The owner role is subject to the forced policy: lift it for the delete, as 0064 does.
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DELETE FROM {TABLE} WHERE analyser = 'local'")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {CHECK}, ALTER COLUMN analyser DROP DEFAULT, "
        f"ADD CONSTRAINT {CHECK} CHECK (analyser = 'spotdown')"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {TABLE} DROP CONSTRAINT {CHECK}, "
        f"ALTER COLUMN analyser SET DEFAULT 'local', "
        f"ADD CONSTRAINT {CHECK} CHECK (analyser IN ('local', 'spotdown'))"
    )
