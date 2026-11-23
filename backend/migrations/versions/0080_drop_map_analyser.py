"""swedebeat_track_maps and swedebeat_mix_maps: no ``analyser`` column (ADR 0040 amendment; #1299)

Revision ID: 0080_drop_map_analyser
Revises: 0079_song_budget_unavailable
Create Date: 2026-11-23

spotdown is the only analysis path (ADR 0040 amendment "spotdown is the only analysis path", item
6). Since 0075 and 0077 the ``analyser`` column of both map caches admits only ``spotdown``, so it
says nothing: it is dropped, with its check, and the unique keys lose it again (back to the keys
0053 and 0018 made: ``(owner_id, reference, analyser_version)`` and ``(owner_id, mix_id)``; one row
per track version and per mix, as before). The ``*_provenance_spotdown`` checks
(``(analyser = 'spotdown') = (provenance IS NOT NULL)``) become ``provenance NOT NULL``: every row
is spotdown's analysis with its record. No row changes (each is a ``spotdown`` row with its
provenance, which those checks guaranteed). ``analyser_version`` (the library's track map version)
stays.

The downgrade restores 0079's shape: the column (every row ``spotdown``, no default), its check,
the provenance checks and the keys with the column, and ``provenance`` nullable again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0080_drop_map_analyser"
down_revision: str | Sequence[str] | None = "0079_song_budget_unavailable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, key with the analyser (0064, 0065), key without it (0053, 0018), its columns)
TABLES = (
    (
        "swedebeat_track_maps",
        "swedebeat_track_maps_owner_reference_version_analyser_key",
        "swedebeat_track_maps_owner_id_reference_analyser_version_key",
        "owner_id, reference, analyser_version",
    ),
    (
        "swedebeat_mix_maps",
        "swedebeat_mix_maps_owner_mix_analyser_key",
        "swedebeat_mix_maps_owner_id_mix_id_key",
        "owner_id, mix_id",
    ),
)


def upgrade() -> None:
    for table, with_analyser, without, columns in TABLES:
        op.execute(
            f"ALTER TABLE public.{table} "
            f"DROP CONSTRAINT {with_analyser}, "
            f"DROP CONSTRAINT {table}_provenance_spotdown, "
            f"DROP CONSTRAINT {table}_analyser, "
            "DROP COLUMN analyser, "
            "ALTER COLUMN provenance SET NOT NULL, "
            f"ADD CONSTRAINT {without} UNIQUE ({columns})"
        )


def downgrade() -> None:
    for table, with_analyser, without, columns in TABLES:
        op.execute(
            f"ALTER TABLE public.{table} "
            f"DROP CONSTRAINT {without}, "
            "ALTER COLUMN provenance DROP NOT NULL, "
            "ADD COLUMN analyser text NOT NULL DEFAULT 'spotdown' "
            f"CONSTRAINT {table}_analyser CHECK (analyser = 'spotdown'), "
            f"ADD CONSTRAINT {table}_provenance_spotdown "
            "CHECK ((analyser = 'spotdown') = (provenance IS NOT NULL)), "
            f"ADD CONSTRAINT {with_analyser} UNIQUE ({columns}, analyser)"
        )
        op.execute(f"ALTER TABLE public.{table} ALTER COLUMN analyser DROP DEFAULT")
