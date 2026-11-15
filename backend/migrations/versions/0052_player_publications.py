"""Send to player and the removal on class delete (#1060, ADR 0034 section 5)

Revision ID: 0052_player_publications
Revises: 0051_player_events
Create Date: 2026-11-15

- ``player_publications``: owned, row security forced. One row per class the user ever sent to
  classbeat-player: its publication counter (``last_number``), the revision and document hash
  the player last applied, and ``removed_at`` once a removal was taken for it. ``source_class_id``
  has **no foreign key to ``classes``**, so the row (and its counter) outlives the class: a
  late send of a lower number stays stale on the player. Unique on ``(owner_id,
  source_class_id)``. ``last_number`` stays below ``2^62`` (the player's bound).
- ``jobs.class_id`` becomes nullable for ``player_remove`` only (amends ADR 0005 section 1):
  ``CHECK (class_id IS NOT NULL OR kind = 'player_remove')``. The composite foreign key to
  ``classes`` is not checked for a NULL, and ``jobs_one_open_per_class`` ignores it.
- ``external_operations.kind`` gains ``player_send`` (carries its class, cascades with it) and
  ``player_remove`` (no class: it is made by the class's deletion).
- ``player_ahead`` and ``player_conflict`` join the ``jobs`` and ``external_operations``
  category checks.

The downgrade deletes the removal jobs and the player operations (the old checks refuse them)
with row security lifted for those statements, as 0018 does.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0052_player_publications"
down_revision: str | Sequence[str] | None = "0051_player_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0029.
OLD_JOB_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed feature_off analysis_limit handoff_expired program_other_sport "
    "music_changed"
)
# The external_operations check of migration 0006.
OLD_OPERATION_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small"
)
NEW_CATEGORIES = _quoted("player_ahead player_conflict")
# The jobs check from here on (``classbeat.jobs.errors.CATEGORIES``, ``tests/test_jobs_errors.py``).
CATEGORIES = f"{OLD_JOB_CATEGORIES}, {NEW_CATEGORIES}"
# The external_operations kind check of migration 0018.
OLD_KINDS = _quoted(
    "mowl_attach_spotify_playlist mowl_save_workout mowl_update_workout spotify_create_playlist "
    "spotify_replace_items mowl_attach_swedebeat_mix"
)
PLAYER_KINDS = _quoted("player_send player_remove")
ATTACH = _quoted("mowl_attach_spotify_playlist mowl_attach_swedebeat_mix")
OLD_CLASS_CHECK = (
    f"(kind NOT IN ({ATTACH}) OR class_id IS NULL) "
    "AND (kind NOT IN ('mowl_save_workout', 'mowl_update_workout') OR class_id IS NOT NULL)"
)
CLASS_CHECK = (
    f"{OLD_CLASS_CHECK} AND (kind <> 'player_send' OR class_id IS NOT NULL) "
    "AND (kind <> 'player_remove' OR class_id IS NULL)"
)
MAX_NUMBER = 2**62

PLAYER_PUBLICATIONS = f"""
    CREATE TABLE public.player_publications (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        source_class_id uuid NOT NULL,
        last_number bigint NOT NULL DEFAULT 0 CONSTRAINT player_publications_last_number
            CHECK (last_number >= 0 AND last_number < {MAX_NUMBER}),
        sent_revision_id uuid,
        sent_document_hash bytea CONSTRAINT player_publications_sent_document_hash
            CHECK (octet_length(sent_document_hash) = 32),
        removed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT player_publications_sent
            CHECK ((sent_revision_id IS NULL) = (sent_document_hash IS NULL)),
        CONSTRAINT player_publications_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT player_publications_owner_id_source_class_id_key
            UNIQUE (owner_id, source_class_id)
    )"""


def _replace(table: str, constraint: str, expression: str) -> list[str]:
    return [
        f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}",
        f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} CHECK ({expression})",
    ]


def _force_rls(table: str, forced: bool) -> str:
    return f"ALTER TABLE public.{table} {'FORCE' if forced else 'NO FORCE'} ROW LEVEL SECURITY"


def _checks(job_categories: str, operation_categories: str, kinds: str, classes: str) -> list[str]:
    return [
        *_replace("jobs", "jobs_error_category", f"error_category IN ({job_categories})"),
        *_replace(
            "external_operations",
            "external_operations_error_category",
            f"error_category IN ({operation_categories})",
        ),
        *_replace("external_operations", "external_operations_kind", f"kind IN ({kinds})"),
        *_replace("external_operations", "external_operations_class", classes),
    ]


def upgrade() -> None:
    for statement in _checks(
        CATEGORIES,
        f"{OLD_OPERATION_CATEGORIES}, {NEW_CATEGORIES}",
        f"{OLD_KINDS}, {PLAYER_KINDS}",
        CLASS_CHECK,
    ):
        op.execute(statement)
    op.execute("ALTER TABLE public.jobs ALTER COLUMN class_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE public.jobs ADD CONSTRAINT jobs_class_id "
        "CHECK (class_id IS NOT NULL OR kind = 'player_remove')"
    )
    op.execute(PLAYER_PUBLICATIONS)
    enable_owner_rls("player_publications")


def downgrade() -> None:
    op.execute("DROP TABLE public.player_publications")
    for table in ("jobs", "external_operations"):
        op.execute(_force_rls(table, False))
    op.execute(f"DELETE FROM public.external_operations WHERE kind IN ({PLAYER_KINDS})")
    op.execute("DELETE FROM public.jobs WHERE class_id IS NULL")
    op.execute(
        f"UPDATE public.jobs SET error_category = 'internal' "
        f"WHERE error_category IN ({NEW_CATEGORIES})"
    )
    for table in ("jobs", "external_operations"):
        op.execute(_force_rls(table, True))
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_class_id")
    op.execute("ALTER TABLE public.jobs ALTER COLUMN class_id SET NOT NULL")
    for statement in _checks(
        OLD_JOB_CATEGORIES, OLD_OPERATION_CATEGORIES, OLD_KINDS, OLD_CLASS_CHECK
    ):
        op.execute(statement)
