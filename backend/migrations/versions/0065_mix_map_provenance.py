"""SwedeBeat mix maps analysed by spotdown, with their provenance (ADR 0040 sections 5 and 6;
SD-P7a, #1304)

Revision ID: 0065_mix_map_provenance
Revises: 0064_track_map_provenance
Create Date: 2026-11-20

``swedebeat_mix_maps`` gains ``analyser`` (``local``: ``design.mixanalysis`` in the sandboxed
child, every row so far, the default; or ``spotdown``: spotdown's ``analysis-v1`` of the fetched
mix, mapped by ``mix_map_policy``) and ``provenance`` (for a ``spotdown`` row and only for one:
``mix_map_model``'s record, 64 KiB at most; no title, mix id, link, file name, URL, scope or client
ref). The key becomes ``(owner_id, mix_id, analyser)``: a mix's ``local`` and ``spotdown`` maps
are two rows, and a job storing one never replaces the other (a class reads the analysis it was
designed on, ``design.mix_cache``). The owner RLS, the 30-day and 20-row bounds and the cascades
are unchanged.

The downgrade drops the ``spotdown`` rows (a cache: they are analysed again), both columns, and
restores the old key.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0065_mix_map_provenance"
down_revision: str | Sequence[str] | None = "0064_track_map_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEY = "swedebeat_mix_maps_owner_id_mix_id_key"
NEW_KEY = "swedebeat_mix_maps_owner_mix_analyser_key"

MIX_MAPS = (
    "ALTER TABLE public.swedebeat_mix_maps "
    "ADD COLUMN analyser text NOT NULL DEFAULT 'local' "
    "CONSTRAINT swedebeat_mix_maps_analyser CHECK (analyser IN ('local', 'spotdown')), "
    "ADD COLUMN provenance jsonb "
    "CONSTRAINT swedebeat_mix_maps_provenance CHECK ("
    "jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 65536), "
    "ADD CONSTRAINT swedebeat_mix_maps_provenance_spotdown "
    "CHECK ((analyser = 'spotdown') = (provenance IS NOT NULL)), "
    f"DROP CONSTRAINT {OLD_KEY}, "
    f"ADD CONSTRAINT {NEW_KEY} UNIQUE (owner_id, mix_id, analyser)"
)


def upgrade() -> None:
    op.execute(MIX_MAPS)


def downgrade() -> None:
    # The owner role is subject to the forced policy: lift it for the repair, as 0053 does.
    op.execute("ALTER TABLE public.swedebeat_mix_maps NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM public.swedebeat_mix_maps WHERE analyser = 'spotdown'")
    op.execute("ALTER TABLE public.swedebeat_mix_maps FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE public.swedebeat_mix_maps "
        f"DROP CONSTRAINT {NEW_KEY}, "
        "DROP CONSTRAINT swedebeat_mix_maps_provenance_spotdown, "
        "DROP COLUMN provenance, DROP COLUMN analyser, "
        f"ADD CONSTRAINT {OLD_KEY} UNIQUE (owner_id, mix_id)"
    )
