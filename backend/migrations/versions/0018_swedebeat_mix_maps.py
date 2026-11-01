"""Swedebeat mixes: the attach operation, its link and the analysis table (ADR 0016 sections 3, 8; A2)

Revision ID: 0018_swedebeat_mix_maps
Revises: 0017_dismissed_rules
Create Date: 2026-11-01

- ``external_operations.kind`` allows ``mowl_attach_swedebeat_mix``; like the Spotify attach it
  belongs to no class (the ``external_operations_class`` check).
- ``mowl_playlist_links`` allows ``source = 'swedebeat'``: the link of a Swedebeat mix to the MOWL
  playlist that wraps it (``source_id`` is the mix's opaque id, checked per source).
- ``swedebeat_mix_maps`` (ADR 0016 section 3): the user's own analysis of one of their mixes, unique
  per ``(owner_id, mix_id)``, owner RLS, no audio and nothing Spotify-derived. The 20-per-user cap,
  the 30 days and the deletion paths are the application's (A3 writes the rows); the table's
  checks bound each row.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0018_swedebeat_mix_maps"
down_revision: str | Sequence[str] | None = "0017_dismissed_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'mowl_attach_spotify_playlist', 'mowl_save_workout', 'mowl_update_workout', "
    "'spotify_create_playlist', 'spotify_replace_items'"
)
NEW_KINDS = f"{OLD_KINDS}, 'mowl_attach_swedebeat_mix'"

# The mix id is the opaque ``ID`` of MOWL's Swedebeat listing; the Spotify id keeps its shape.
SPOTIFY_ID = "source_id ~ '^[0-9A-Za-z]{22}$'"
MIX_ID = "source_id ~ '^[0-9A-Za-z_-]{1,64}$'"
MIX_ID_COLUMN = (
    "mix_id text NOT NULL "
    "CONSTRAINT swedebeat_mix_maps_mix_id CHECK (mix_id ~ '^[0-9A-Za-z_-]{1,64}$')"
)

SWEDEBEAT_MIX_MAPS = f"""
    CREATE TABLE public.swedebeat_mix_maps (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        {MIX_ID_COLUMN},
        mowl_connection_id uuid NOT NULL,
        playlist_id bigint NOT NULL
            CONSTRAINT swedebeat_mix_maps_playlist_id CHECK (playlist_id > 0),
        name text NOT NULL
            CONSTRAINT swedebeat_mix_maps_name CHECK (char_length(name) <= 200),
        duration double precision NOT NULL
            CONSTRAINT swedebeat_mix_maps_duration CHECK (duration > 0 AND duration <= 7200),
        listed_duration double precision NOT NULL
            CONSTRAINT swedebeat_mix_maps_listed_duration CHECK (listed_duration >= 0),
        map jsonb NOT NULL
            CONSTRAINT swedebeat_mix_maps_map CHECK (octet_length(map::text) <= 1048576),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT swedebeat_mix_maps_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT swedebeat_mix_maps_owner_id_mix_id_key UNIQUE (owner_id, mix_id)
    )"""

INDEXES = (
    "CREATE INDEX swedebeat_mix_maps_owner_created ON public.swedebeat_mix_maps "
    "(owner_id, created_at)",
)


def _replace(table: str, constraint: str, expression: str) -> list[str]:
    return [
        f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}",
        f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} CHECK ({expression})",
    ]


def _force_rls(table: str, forced: bool) -> str:
    return f"ALTER TABLE public.{table} {'FORCE' if forced else 'NO FORCE'} ROW LEVEL SECURITY"


def _class_check(kinds: str) -> str:
    return (
        f"(kind NOT IN ({kinds}) OR class_id IS NULL) "
        "AND (kind NOT IN ('mowl_save_workout', 'mowl_update_workout') OR class_id IS NOT NULL)"
    )


def upgrade() -> None:
    attach = "'mowl_attach_spotify_playlist', 'mowl_attach_swedebeat_mix'"
    for statement in (
        *_replace("external_operations", "external_operations_kind", f"kind IN ({NEW_KINDS})"),
        *_replace("external_operations", "external_operations_class", _class_check(attach)),
        *_replace("mowl_playlist_links", "mowl_playlist_links_source", "source IN ('spotify', 'swedebeat')"),
        *_replace(
            "mowl_playlist_links",
            "mowl_playlist_links_source_id",
            f"(source = 'spotify' AND {SPOTIFY_ID}) OR (source = 'swedebeat' AND {MIX_ID})",
        ),
    ):
        op.execute(statement)
    op.execute(SWEDEBEAT_MIX_MAPS)
    enable_owner_rls("swedebeat_mix_maps")
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE public.swedebeat_mix_maps")  # nothing refers to it
    # The owner role is subject to the forced policies: lift them for the repairs, as 0006 does.
    for table in ("mowl_playlist_links", "external_operations"):
        op.execute(_force_rls(table, False))
    op.execute("DELETE FROM public.mowl_playlist_links WHERE source = 'swedebeat'")
    op.execute("DELETE FROM public.external_operations WHERE kind = 'mowl_attach_swedebeat_mix'")
    for table in ("mowl_playlist_links", "external_operations"):
        op.execute(_force_rls(table, True))
    for statement in (
        *_replace("mowl_playlist_links", "mowl_playlist_links_source_id", SPOTIFY_ID),
        *_replace("mowl_playlist_links", "mowl_playlist_links_source", "source IN ('spotify')"),
        *_replace(
            "external_operations",
            "external_operations_class",
            _class_check("'mowl_attach_spotify_playlist'"),
        ),
        *_replace("external_operations", "external_operations_kind", f"kind IN ({OLD_KINDS})"),
    ):
        op.execute(statement)
