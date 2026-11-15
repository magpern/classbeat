"""SwedeBeat studio: the per-owner track analysis cache and its daily limit (ADR 0036 §4.1; SR C4)

Revision ID: 0053_swedebeat_track_maps
Revises: 0052_player_publications
Create Date: 2026-11-15

- ``swedebeat_track_maps``: the user's own analysis of one catalogue track (``mowl.track``), unique
  on ``(owner_id, reference, analyser_version)``, with the SHA-256 of the analysed bytes. Owner RLS,
  no audio. The reference has the gateway's shape (``^[A-Za-z0-9_-]{1,64}$``), the analyser version
  is ``mowl.track.TRACK_VERSION`` (positive), the map is bounded as ``swedebeat_mix_maps.map`` is.
  The 30 days, the per-user cap and the deletion with the SwedeBeat connection are the
  application's (``classbeat.design.track_cache``, ``classbeat.connections.forget``).
- ``swedebeat_track_downloads``: one row per track download the worker starts (a cache miss),
  written before the download, so the daily limit counts every attempt. No content: the owner and
  the time. Owner RLS; gone with the account only (a Disconnect never gives a fresh day).
- ``jobs.error_category`` gains ``track_too_large`` (the 60 MB cap or the gateway's ``413``),
  ``track_unavailable`` (the gateway has no file for the reference) and ``track_limit`` (the daily
  limit of track downloads). None is retried.

The downgrade drops both tables and turns the new categories into ``internal`` with row security
lifted for that statement, as 0052 does.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0053_swedebeat_track_maps"
down_revision: str | Sequence[str] | None = "0052_player_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0052.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed feature_off analysis_limit handoff_expired program_other_sport "
    "music_changed player_ahead player_conflict"
)
NEW_CATEGORIES = _quoted("track_too_large track_unavailable track_limit")
# The jobs check from here on (``classbeat.jobs.errors.CATEGORIES``, ``tests/test_jobs_errors.py``).
CATEGORIES = f"{OLD_CATEGORIES}, {NEW_CATEGORIES}"

SWEDEBEAT_TRACK_MAPS = """
    CREATE TABLE public.swedebeat_track_maps (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        reference text NOT NULL
            CONSTRAINT swedebeat_track_maps_reference CHECK (reference ~ '^[A-Za-z0-9_-]{1,64}$'),
        analyser_version integer NOT NULL
            CONSTRAINT swedebeat_track_maps_analyser_version CHECK (analyser_version > 0),
        sha256 bytea NOT NULL
            CONSTRAINT swedebeat_track_maps_sha256 CHECK (octet_length(sha256) = 32),
        map jsonb NOT NULL
            CONSTRAINT swedebeat_track_maps_map CHECK (octet_length(map::text) <= 1048576),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT swedebeat_track_maps_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT swedebeat_track_maps_owner_id_reference_analyser_version_key
            UNIQUE (owner_id, reference, analyser_version)
    )"""
SWEDEBEAT_TRACK_DOWNLOADS = """
    CREATE TABLE public.swedebeat_track_downloads (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT swedebeat_track_downloads_owner_id_id_key UNIQUE (owner_id, id)
    )"""
INDEXES = (
    "CREATE INDEX swedebeat_track_maps_owner_created ON public.swedebeat_track_maps "
    "(owner_id, created_at)",
    "CREATE INDEX swedebeat_track_downloads_owner_created ON public.swedebeat_track_downloads "
    "(owner_id, created_at)",
)


def _categories(values: str) -> None:
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category")
    op.execute(
        f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({values}))"
    )


def upgrade() -> None:
    op.execute(SWEDEBEAT_TRACK_MAPS)
    enable_owner_rls("swedebeat_track_maps")
    op.execute(SWEDEBEAT_TRACK_DOWNLOADS)
    enable_owner_rls("swedebeat_track_downloads")
    for statement in INDEXES:
        op.execute(statement)
    _categories(CATEGORIES)


def downgrade() -> None:
    op.execute("DROP TABLE public.swedebeat_track_downloads")  # nothing refers to either
    op.execute("DROP TABLE public.swedebeat_track_maps")
    # The owner role is subject to the forced policy: lift it for the repair, as 0052 does.
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"UPDATE public.jobs SET error_category = 'internal' "
        f"WHERE error_category IN ({NEW_CATEGORIES})"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    _categories(OLD_CATEGORIES)
