"""Studio track maps analysed by spotdown, with their provenance (ADR 0040 sections 6 and 7; SD-P5,
#1302)

Revision ID: 0064_track_map_provenance
Revises: 0063_spotdown_shadow
Create Date: 2026-11-20

``swedebeat_track_maps`` gains two columns; the key stays ``(owner_id, reference,
analyser_version)``, so a track has one current map, whichever analysed it:

- ``analyser``: ``local`` (``mowl.music.analyze_track`` in the sandboxed child, every row so far)
  or ``spotdown`` (spotdown's ``analysis-v1`` of the uploaded bytes, mapped by
  ``spotdown_map_policy``); the default is ``local``.
- ``provenance``: for a ``spotdown`` row, and only for one, ``provenance_model``'s record (the
  analysis id, the analyser's versions, the audio identity, the timing class with its reason and
  evidence, the policy versions; 64 KiB at most). No title, reference, file name, URL, scope or
  client ref.

The downgrade drops the ``spotdown`` rows (a cache: they are analysed again) and both columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0064_track_map_provenance"
down_revision: str | Sequence[str] | None = "0063_spotdown_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = (
    "ALTER TABLE public.swedebeat_track_maps "
    "ADD COLUMN analyser text NOT NULL DEFAULT 'local' "
    "CONSTRAINT swedebeat_track_maps_analyser CHECK (analyser IN ('local', 'spotdown')), "
    "ADD COLUMN provenance jsonb "
    "CONSTRAINT swedebeat_track_maps_provenance CHECK ("
    "jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 65536), "
    "ADD CONSTRAINT swedebeat_track_maps_provenance_spotdown "
    "CHECK ((analyser = 'spotdown') = (provenance IS NOT NULL))",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # The owner role is subject to the forced policy: lift it for the repair, as 0053 does.
    op.execute("ALTER TABLE public.swedebeat_track_maps NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM public.swedebeat_track_maps WHERE analyser = 'spotdown'")
    op.execute("ALTER TABLE public.swedebeat_track_maps FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE public.swedebeat_track_maps "
        "DROP CONSTRAINT swedebeat_track_maps_provenance_spotdown, "
        "DROP COLUMN provenance, DROP COLUMN analyser"
    )
