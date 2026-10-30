"""Spotify connection data, the pool consent and class playlists (ADR 0008 s. 1, ADR 0009 s. 3, 5)

Revision ID: 0006_spotify
Revises: 0005_classes
Create Date: 2026-10-30

M4's only migration, issue SP1 (#174). Every new table is owned (ADR 0002 section 2): ``owner_id``
referencing ``users``, ``UNIQUE (owner_id, id)``, owner-inclusive foreign keys, every other unique
constraint including ``owner_id``, ``enable_owner_rls``.

- ``spotify_oauth_flows`` (ADR 0009 section 3): one in-flight connect. ADR 0009 writes
  ``state_hash`` as ``UNIQUE``; guard 1 requires every unique index of an owned table to include
  ``owner_id``, so it is ``UNIQUE (owner_id, state_hash)``. The callback's take always names the
  owner (and the session), and 32 random bytes do not collide, so nothing is lost. A flow lives at
  most 10 minutes (a check, whatever the TTL setting says).
- ``spotify_pool`` (ADR 0008 section 1): the standing consent, one row per user.
- ``class_playlists`` (ADR 0008 section 1): the final playlists classbeat created for a class. As in
  0005, the revision pointer is keyed on ``(owner, class, revision)``, so it can only name a
  revision of the same class.
- ``connections.generation`` (ADR 0009 section 5.4): the connection version that ``hold`` and the
  status flips compare; a user's connect, reconnect or Client ID change increases it, a refresh or
  a rewrap does not.
- ``external_operations.kind`` gains the two Spotify writes; the three category checks gain ADR
  0009 section 8's and ADR 0008 section 1's categories, ``connections.last_error`` ADR 0009's.

The queue functions (``docker/postgres/queue/20-queue.sql``) read no column this changes, so their
version and digests stay.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0006_spotify"
down_revision: str | Sequence[str] | None = "0005_classes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs, external_operations and agent_runs checks of migration 0005.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked"
)
# ADR 0009 section 8 (``rate_limited`` and ``upstream_unavailable`` exist already).
SPOTIFY_CONNECTION_CATEGORIES = (
    "invalid_grant app_misconfigured not_allowlisted forbidden quota_exhausted not_found "
    "scope_mismatch invalid_response account_changed access_denied"
)
# ADR 0008 section 1.
SONG_CATEGORIES = (
    "consent_missing pool_unusable pool_missing pool_not_synced playlist_mismatch pool_too_small"
)
NEW_ONLY = _quoted(f"{SPOTIFY_CONNECTION_CATEGORIES} {SONG_CATEGORIES}")
CATEGORIES = f"{OLD_CATEGORIES}, {NEW_ONLY}"
# The connections.last_error check of migration 0003, and what ADR 0009 section 8 adds to it.
OLD_LAST_ERRORS = _quoted(
    "invalid_credentials needs_reconnect rate_limited upstream_unavailable key_unavailable "
    "decrypt_failed not_connected unknown"
)
NEW_LAST_ERRORS = _quoted(SPOTIFY_CONNECTION_CATEGORIES)
LAST_ERRORS = f"{OLD_LAST_ERRORS}, {NEW_LAST_ERRORS}"
OLD_KINDS = _quoted("mowl_attach_spotify_playlist mowl_save_workout mowl_update_workout")
SPOTIFY_KINDS = _quoted("spotify_create_playlist spotify_replace_items")
KINDS = f"{OLD_KINDS}, {SPOTIFY_KINDS}"

SHA256 = "octet_length({0}) = 32"
PLAYLIST_ID = "{0} ~ '^[0-9A-Za-z]{{22}}$'"
# ADR 0009 section 5: the Spotify user id is 1 to 100 printable characters.
SPOTIFY_USER_ID = "char_length({0}) BETWEEN 1 AND 100 AND {0} !~ '[[:cntrl:]]'"


def _owner_columns() -> str:
    return """
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,"""


SPOTIFY_OAUTH_FLOWS = f"""
    CREATE TABLE public.spotify_oauth_flows ({_owner_columns()}
        state_hash bytea NOT NULL
            CONSTRAINT spotify_oauth_flows_state_hash CHECK ({SHA256.format("state_hash")}),
        session_hash bytea NOT NULL
            CONSTRAINT spotify_oauth_flows_session_hash CHECK ({SHA256.format("session_hash")}),
        client_id text NOT NULL
            CONSTRAINT spotify_oauth_flows_client_id CHECK (client_id ~ '^[0-9a-f]{{32}}$'),
        redirect_uri text NOT NULL CONSTRAINT spotify_oauth_flows_redirect_uri
            CHECK (char_length(redirect_uri) BETWEEN 1 AND 2048),
        verifier text NOT NULL
            CONSTRAINT spotify_oauth_flows_verifier CHECK (verifier ~ '^[A-Za-z0-9_-]{{43}}$'),
        confirm_account_change boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        CONSTRAINT spotify_oauth_flows_lifetime CHECK (
            expires_at > created_at AND expires_at <= created_at + interval '10 minutes'),
        CONSTRAINT spotify_oauth_flows_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT spotify_oauth_flows_owner_id_state_hash_key UNIQUE (owner_id, state_hash)
    )"""

SPOTIFY_POOL = f"""
    CREATE TABLE public.spotify_pool ({_owner_columns()}
        spotify_user_id text NOT NULL CONSTRAINT spotify_pool_spotify_user_id
            CHECK ({SPOTIFY_USER_ID.format("spotify_user_id")}),
        playlist_id text
            CONSTRAINT spotify_pool_playlist_id CHECK ({PLAYLIST_ID.format("playlist_id")}),
        consented_at timestamptz NOT NULL DEFAULT now(),
        consent_version integer NOT NULL
            CONSTRAINT spotify_pool_consent_version CHECK (consent_version >= 1),
        last_hash bytea CONSTRAINT spotify_pool_last_hash CHECK ({SHA256.format("last_hash")}),
        last_written_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT spotify_pool_written CHECK (playlist_id IS NOT NULL OR last_hash IS NULL),
        CONSTRAINT spotify_pool_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT spotify_pool_owner_id_key UNIQUE (owner_id)
    )"""

CLASS_PLAYLISTS = f"""
    CREATE TABLE public.class_playlists ({_owner_columns()}
        class_id uuid NOT NULL,
        revision_id uuid,
        playlist_id text NOT NULL
            CONSTRAINT class_playlists_playlist_id CHECK ({PLAYLIST_ID.format("playlist_id")}),
        spotify_user_id text NOT NULL CONSTRAINT class_playlists_spotify_user_id
            CHECK ({SPOTIFY_USER_ID.format("spotify_user_id")}),
        name text NOT NULL
            CONSTRAINT class_playlists_name CHECK (char_length(name) BETWEEN 1 AND 100),
        state text NOT NULL
            CONSTRAINT class_playlists_state CHECK (state IN ('created', 'verified')),
        content_hash bytea
            CONSTRAINT class_playlists_content_hash CHECK ({SHA256.format("content_hash")}),
        track_count integer
            CONSTRAINT class_playlists_track_count CHECK (track_count BETWEEN 0 AND 1000),
        mowl_playlist_id bigint
            CONSTRAINT class_playlists_mowl_playlist_id CHECK (mowl_playlist_id > 0),
        create_operation_id uuid,
        detached_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT class_playlists_verified CHECK (
            state <> 'verified' OR (content_hash IS NOT NULL AND track_count IS NOT NULL)),
        CONSTRAINT class_playlists_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT class_playlists_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT class_playlists_revision_fkey FOREIGN KEY (owner_id, class_id, revision_id)
            REFERENCES public.class_revisions (owner_id, class_id, id)
            ON DELETE SET NULL (revision_id),
        CONSTRAINT class_playlists_create_operation_fkey FOREIGN KEY (owner_id, create_operation_id)
            REFERENCES public.external_operations (owner_id, id)
            ON DELETE SET NULL (create_operation_id)
    )"""

INDEXES = (
    # One live row per playlist and owner (a detached row is history and frees the id).
    "CREATE UNIQUE INDEX class_playlists_live_playlist ON public.class_playlists "
    "(owner_id, playlist_id) WHERE detached_at IS NULL",
    # Foreign-key columns whose parent deletion runs a SET NULL or a cascade.
    "CREATE INDEX class_playlists_owner_class ON public.class_playlists (owner_id, class_id)",
    "CREATE INDEX class_playlists_owner_revision "
    "ON public.class_playlists (owner_id, revision_id)",
    "CREATE INDEX class_playlists_owner_operation "
    "ON public.class_playlists (owner_id, create_operation_id)",
    # The oldest of a user's flows (at most 3 unexpired, ADR 0009 section 3) and the purge.
    "CREATE INDEX spotify_oauth_flows_owner_created "
    "ON public.spotify_oauth_flows (owner_id, created_at)",
)


def _replace_check(table: str, constraint: str, column: str, values: str) -> list[str]:
    return [
        f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}",
        f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} "
        f"CHECK ({column} IN ({values}))",
    ]


def _category_checks(categories: str, kinds: str, last_errors: str) -> list[str]:
    return [
        *_replace_check("external_operations", "external_operations_kind", "kind", kinds),
        *_replace_check(
            "external_operations", "external_operations_error_category", "error_category",
            categories,
        ),
        *_replace_check("jobs", "jobs_error_category", "error_category", categories),
        *_replace_check(
            "agent_runs", "agent_runs_outcome", "outcome", f"'submitted', 'cancelled', {categories}"
        ),
        *_replace_check("connections", "connections_last_error", "last_error", last_errors),
    ]  # fmt: skip


def _force_rls(table: str, forced: bool) -> str:
    return f"ALTER TABLE public.{table} {'FORCE' if forced else 'NO FORCE'} ROW LEVEL SECURITY"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.connections ADD COLUMN generation bigint NOT NULL DEFAULT 1 "
        "CONSTRAINT connections_generation CHECK (generation >= 1)"
    )
    for statement in _category_checks(CATEGORIES, KINDS, LAST_ERRORS):
        op.execute(statement)
    op.execute(SPOTIFY_OAUTH_FLOWS)
    enable_owner_rls("spotify_oauth_flows")
    op.execute(SPOTIFY_POOL)
    enable_owner_rls("spotify_pool")
    op.execute(CLASS_PLAYLISTS)
    enable_owner_rls("class_playlists")
    for statement in INDEXES:
        op.execute(statement)


# What the older schema can't hold, per table: the Spotify writes go, the new categories become
# ``internal`` (jobs, operations, usage) or ``unknown`` (a connection's last error).
_DOWNGRADE_REPAIRS = (
    ("external_operations", f"DELETE FROM public.external_operations WHERE kind IN ({SPOTIFY_KINDS})"),
    ("external_operations", "UPDATE public.external_operations SET error_category = 'internal' "
     f"WHERE error_category IN ({NEW_ONLY})"),
    ("jobs", f"UPDATE public.jobs SET error_category = 'internal' WHERE error_category IN ({NEW_ONLY})"),
    ("agent_runs", f"UPDATE public.agent_runs SET outcome = 'internal' WHERE outcome IN ({NEW_ONLY})"),
    ("connections", "UPDATE public.connections SET last_error = 'unknown' "
     f"WHERE last_error IN ({NEW_LAST_ERRORS})"),
)  # fmt: skip


def downgrade() -> None:
    op.execute("DROP TABLE public.class_playlists")
    op.execute("DROP TABLE public.spotify_pool")
    op.execute("DROP TABLE public.spotify_oauth_flows")
    # The owner is exempt from row security while it is not forced, so the repairs reach every
    # user's rows (as in 0005); forced again before the migration ends.
    tables = sorted({table for table, _ in _DOWNGRADE_REPAIRS})
    for table in tables:
        op.execute(_force_rls(table, False))
    # ``agent_runs`` refuses changes to finished rows (its restrictive policy); with FORCE off the
    # table owner is exempt from that policy too.
    for _, statement in _DOWNGRADE_REPAIRS:
        op.execute(statement)
    for table in tables:
        op.execute(_force_rls(table, True))
    for statement in _category_checks(OLD_CATEGORIES, OLD_KINDS, OLD_LAST_ERRORS):
        op.execute(statement)
    op.execute("ALTER TABLE public.connections DROP COLUMN generation")
