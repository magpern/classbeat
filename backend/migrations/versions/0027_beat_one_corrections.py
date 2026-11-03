"""Beat-one corrections per class (ADR 0007 amendment "beat-one corrections", #535, BC1)

Revision ID: 0027_beat_one_corrections
Revises: 0026_plan_gates
Create Date: 2026-11-03

- ``beat_one_corrections``: one row per song of a class's music whose beat 1 the user moved by
  whole beats, keyed by the fingerprint of the music it was set on. Owned and class-owned: owner
  RLS, gone with the class and the account. No title, no provider id, no map: nothing shared, and
  no analysis is ever written by a correction.
- ``jobs.error_category`` gains ``music_changed``: a ``refit_draft`` whose music's fingerprint is
  no longer the base revision's. It is raised before anything is stored and is not retried.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0027_beat_one_corrections"
down_revision: str | Sequence[str] | None = "0026_plan_gates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0024.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed feature_off analysis_limit handoff_expired program_other_sport"
)
CATEGORIES = f"{OLD_CATEGORIES}, 'music_changed'"

BEAT_ONE_CORRECTIONS = """
    CREATE TABLE public.beat_one_corrections (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        fingerprint text NOT NULL
            CONSTRAINT beat_one_corrections_fingerprint CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        song integer NOT NULL
            CONSTRAINT beat_one_corrections_song CHECK (song BETWEEN 1 AND 500),
        beats smallint NOT NULL
            CONSTRAINT beat_one_corrections_beats CHECK (beats BETWEEN -3 AND 3 AND beats <> 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT beat_one_corrections_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT beat_one_corrections_one_per_song
            UNIQUE (owner_id, class_id, fingerprint, song),
        CONSTRAINT beat_one_corrections_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE
    )"""


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category")
    op.execute(
        "ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({values}))"
    )


def upgrade() -> None:
    op.execute(BEAT_ONE_CORRECTIONS)
    enable_owner_rls("beat_one_corrections")
    _replace(CATEGORIES)


def downgrade() -> None:
    op.execute("DROP TABLE public.beat_one_corrections")
    # The owner is exempt from row security while it is not forced (as in 0023 and 0024).
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE public.jobs SET error_category = 'internal' WHERE error_category = 'music_changed'"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    _replace(OLD_CATEGORIES)
