"""SwedeBeat studio: the outputs of an approved studio draft (ADR 0036 §6; SR C7)

Revision ID: 0054_class_outputs
Revises: 0053_swedebeat_track_maps
Create Date: 2026-11-16

- ``class_outputs``: one row per output **attempt** of one approved studio draft, unique on
  ``(owner_id, class_id, revision_id, output, attempt)``. ``output`` is ``spotify_playlist``,
  ``swedebeat_playlist``, ``swedebeat_mix`` or ``mowl``; ``attempt`` starts at 1 ("Create another
  anyway" opens the next); ``state`` is the output's, derived from its external operations
  (``pending``, ``running``, ``done``, ``failed``, ``in_doubt``, and ``published_not_listed`` for a
  MOWL attach that found no listed mix). ``detail`` is what the attempt stored before its first
  write intent (the names, the request body, the render plan and the analysed sha256s) and each
  returned id. Owner RLS forced; goes with its class (and revision) and with the account.
- ``studio_output_starts``: one row per output attempt opened, for the daily limit. No content.
  Owner RLS; gone with the account only (a class delete never gives a fresh day).
- ``external_operations.kind`` gains ``swedebeat_create_playlist``, ``swedebeat_publish_playlist``,
  ``swedebeat_merge_publish`` and ``mowl_attach_studio_mix``; each carries its class.
- ``jobs.error_category`` gains ``connection_changed`` (the approved SwedeBeat connection is gone
  or reconnected), ``writes_off`` (the gateway's ``403``), ``render_not_reproducible`` (a re-render
  whose sha256 is not the first one's) and ``output_limit``; ``external_operations.error_category``
  gains ``writes_off``, ``track_too_large`` and ``track_unavailable`` (a definite refusal of a
  playlist create naming a track over the cap or without a file).

The downgrade drops both tables, deletes the studio operations and turns the new categories into
``internal`` with row security lifted for those statements, as 0052 does.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0054_class_outputs"
down_revision: str | Sequence[str] | None = "0053_swedebeat_track_maps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0053.
OLD_JOB_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed feature_off analysis_limit handoff_expired program_other_sport "
    "music_changed player_ahead player_conflict track_too_large track_unavailable track_limit"
)
NEW_JOB_CATEGORIES = _quoted("connection_changed writes_off render_not_reproducible output_limit")
# The jobs check from here on (``classbeat.jobs.errors.CATEGORIES``, ``tests/test_jobs_errors.py``).
CATEGORIES = f"{OLD_JOB_CATEGORIES}, {NEW_JOB_CATEGORIES}"
# The external_operations check of migration 0052.
OLD_OPERATION_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small player_ahead player_conflict"
)
NEW_OPERATION_CATEGORIES = _quoted("writes_off track_too_large track_unavailable")
OPERATION_CATEGORIES = f"{OLD_OPERATION_CATEGORIES}, {NEW_OPERATION_CATEGORIES}"
# The external_operations kind check of migration 0052.
OLD_KINDS = _quoted(
    "mowl_attach_spotify_playlist mowl_save_workout mowl_update_workout spotify_create_playlist "
    "spotify_replace_items mowl_attach_swedebeat_mix player_send player_remove"
)
STUDIO_KINDS = _quoted(
    "swedebeat_create_playlist swedebeat_publish_playlist swedebeat_merge_publish "
    "mowl_attach_studio_mix"
)
KINDS = f"{OLD_KINDS}, {STUDIO_KINDS}"
# The class check of migration 0052, and from here on the studio writes carry their class.
ATTACH = _quoted("mowl_attach_spotify_playlist mowl_attach_swedebeat_mix")
OLD_CLASS_CHECK = (
    f"(kind NOT IN ({ATTACH}) OR class_id IS NULL) "
    "AND (kind NOT IN ('mowl_save_workout', 'mowl_update_workout') OR class_id IS NOT NULL) "
    "AND (kind <> 'player_send' OR class_id IS NOT NULL) "
    "AND (kind <> 'player_remove' OR class_id IS NULL)"
)
CLASS_CHECK = f"{OLD_CLASS_CHECK} AND (kind NOT IN ({STUDIO_KINDS}) OR class_id IS NOT NULL)"

OUTPUTS = _quoted("spotify_playlist swedebeat_playlist swedebeat_mix mowl")
STATES = _quoted("pending running done failed in_doubt published_not_listed")
MAX_DETAIL_BYTES = 32768

CLASS_OUTPUTS = f"""
    CREATE TABLE public.class_outputs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        revision_id uuid NOT NULL,
        output text NOT NULL CONSTRAINT class_outputs_output CHECK (output IN ({OUTPUTS})),
        attempt integer NOT NULL
            CONSTRAINT class_outputs_attempt CHECK (attempt BETWEEN 1 AND 1000),
        state text NOT NULL CONSTRAINT class_outputs_state CHECK (state IN ({STATES})),
        external_id text
            CONSTRAINT class_outputs_external_id CHECK (char_length(external_id) BETWEEN 1 AND 2048),
        detail jsonb NOT NULL DEFAULT '{{}}'::jsonb CONSTRAINT class_outputs_detail
            CHECK (jsonb_typeof(detail) = 'object'
                   AND octet_length(detail::text) <= {MAX_DETAIL_BYTES}),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT class_outputs_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT class_outputs_owner_id_class_id_revision_id_output_attempt_key
            UNIQUE (owner_id, class_id, revision_id, output, attempt),
        CONSTRAINT class_outputs_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT class_outputs_revision_fkey FOREIGN KEY (owner_id, class_id, revision_id)
            REFERENCES public.class_revisions (owner_id, class_id, id) ON DELETE CASCADE
    )"""
STUDIO_OUTPUT_STARTS = """
    CREATE TABLE public.studio_output_starts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT studio_output_starts_owner_id_id_key UNIQUE (owner_id, id)
    )"""
INDEXES = (
    "CREATE INDEX class_outputs_owner_class ON public.class_outputs (owner_id, class_id)",
    "CREATE INDEX class_outputs_owner_revision ON public.class_outputs "
    "(owner_id, class_id, revision_id)",
    "CREATE INDEX studio_output_starts_owner_created ON public.studio_output_starts "
    "(owner_id, created_at)",
)


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
    for statement in _checks(CATEGORIES, OPERATION_CATEGORIES, KINDS, CLASS_CHECK):
        op.execute(statement)
    op.execute(CLASS_OUTPUTS)
    enable_owner_rls("class_outputs")
    op.execute(STUDIO_OUTPUT_STARTS)
    enable_owner_rls("studio_output_starts")
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE public.studio_output_starts")
    op.execute("DROP TABLE public.class_outputs")
    for table in ("jobs", "external_operations"):
        op.execute(_force_rls(table, False))
    op.execute(f"DELETE FROM public.external_operations WHERE kind IN ({STUDIO_KINDS})")
    op.execute(
        f"UPDATE public.external_operations SET error_category = 'internal' "
        f"WHERE error_category IN ({NEW_OPERATION_CATEGORIES})"
    )
    op.execute(
        f"UPDATE public.jobs SET error_category = 'internal' "
        f"WHERE error_category IN ({NEW_JOB_CATEGORIES})"
    )
    for table in ("jobs", "external_operations"):
        op.execute(_force_rls(table, True))
    for statement in _checks(
        OLD_JOB_CATEGORIES, OLD_OPERATION_CATEGORIES, OLD_KINDS, OLD_CLASS_CHECK
    ):
        op.execute(statement)
