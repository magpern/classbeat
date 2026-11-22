"""The instructor's Spotify alignment corrections, class-owned (ADR 0042 section 7.4; CP-S3 #1398)

Revision ID: 0078_spotify_offset_corrections
Revises: 0077_drop_local_mix_maps
Create Date: 2026-11-22

``spotify_offset_corrections``: one row per song of a class's Spotify music whose alignment the
instructor moved by ear while auditioning (the Spotify Connect transport), keyed by the fingerprint
of the music it was set on. Class-owned exactly as ``beat_one_corrections`` is (0029): ``owner_id``
and an owner-inclusive ``class_id`` foreign key ``ON DELETE CASCADE`` (so the rows go with the
class and with the account), owner RLS (``owner_isolation``, forced), unique per
``(owner_id, class_id, fingerprint, song)``. ``song`` is the 1-based play order, at most 200 (a
draft has at most 200 songs; the application also caps the rows of one fingerprint at 200).

- ``offset_ms`` is the correction alone, never added to spotdown's alignment (a different fact,
  read from the analysis record): at most a minute either way, and never 0 (no correction is no
  row).
- ``set_against_analysis`` records the analysis the instructor listened against, exactly
  ``{"map_version": int or null, "sha256": <the analysed file's sha256>}``. If either differs from
  the class's analysis now, the correction is shown but not applied until confirmed again.

**No Spotify id, no title, no analysis, no map**: nothing shared. A correction never reaches
another class of the user on the same track, and never edits an analysis or a revision (a refit is
the only way it reaches the class).
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0078_spotify_offset_corrections"
down_revision: str | Sequence[str] | None = "0077_drop_local_mix_maps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_SONGS = 200
MAX_OFFSET_MS = 60_000

SPOTIFY_OFFSET_CORRECTIONS = f"""
    CREATE TABLE public.spotify_offset_corrections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        fingerprint text NOT NULL
            CONSTRAINT spotify_offset_corrections_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{{64}}$'),
        song integer NOT NULL
            CONSTRAINT spotify_offset_corrections_song CHECK (song BETWEEN 1 AND {MAX_SONGS}),
        offset_ms integer NOT NULL
            CONSTRAINT spotify_offset_corrections_offset_ms
            CHECK (offset_ms BETWEEN -{MAX_OFFSET_MS} AND {MAX_OFFSET_MS} AND offset_ms <> 0),
        set_against_analysis jsonb NOT NULL
            CONSTRAINT spotify_offset_corrections_set_against CHECK (
                jsonb_typeof(set_against_analysis) = 'object'
                AND (set_against_analysis - 'map_version' - 'sha256') = '{{}}'::jsonb
                AND set_against_analysis ? 'sha256'
                AND (set_against_analysis ->> 'sha256') ~ '^[0-9a-f]{{64}}$'
                AND set_against_analysis ? 'map_version'
                AND jsonb_typeof(set_against_analysis -> 'map_version') IN ('number', 'null')
            ),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT spotify_offset_corrections_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT spotify_offset_corrections_one_per_song
            UNIQUE (owner_id, class_id, fingerprint, song),
        CONSTRAINT spotify_offset_corrections_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE
    )"""


def upgrade() -> None:
    op.execute(SPOTIFY_OFFSET_CORRECTIONS)
    enable_owner_rls("spotify_offset_corrections")


def downgrade() -> None:
    op.execute("DROP TABLE public.spotify_offset_corrections")
