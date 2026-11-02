"""Designing on a Swedebeat mix: the daily analysis limit and two job categories (ADR 0016 §2, §7; A4)

Revision ID: 0020_swedebeat_analyses
Revises: 0019_swedebeat_media_categories
Create Date: 2026-11-01

- ``swedebeat_analyses``: one row per new analysis of a mix the worker starts (a cache miss),
  written before anything is attached or fetched, so the limit of 10 new analyses per user per day
  (ADR 0016 section 7) counts every attempt, a failed fetch included. A row holds no content: the
  owner and the time. Owner RLS; gone with the account; rows older than two days are dropped by
  the next insert of the same user. Not deleted with the MOWL connection or the maps, so neither
  a reconnect nor a forgotten analysis gives the user a fresh day.
- ``jobs.error_category`` gains ``feature_off`` (a ``design_draft`` or ``revise_draft`` on a
  Swedebeat mix whose run finds ``swedebeat_enabled`` off) and ``analysis_limit`` (the daily
  limit was reached by the time the worker would start a new analysis). Neither is retried.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0020_swedebeat_analyses"
down_revision: str | Sequence[str] | None = "0019_swedebeat_media_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0019.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed"
)
NEW = ("feature_off", "analysis_limit")
CATEGORIES = f"{OLD_CATEGORIES}, {', '.join(repr(name) for name in NEW)}"

SWEDEBEAT_ANALYSES = """
    CREATE TABLE public.swedebeat_analyses (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT swedebeat_analyses_owner_id_id_key UNIQUE (owner_id, id)
    )"""


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category")
    op.execute(
        f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({values}))"
    )


def upgrade() -> None:
    op.execute(SWEDEBEAT_ANALYSES)
    enable_owner_rls("swedebeat_analyses")
    op.execute(
        "CREATE INDEX swedebeat_analyses_owner_created ON public.swedebeat_analyses "
        "(owner_id, created_at)"
    )
    _replace(CATEGORIES)


def downgrade() -> None:
    # The owner is exempt from row security while it is not forced (as in 0019).
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    listed = ", ".join(repr(name) for name in NEW)
    op.execute(
        "UPDATE public.jobs SET error_category = 'internal' "
        f"WHERE error_category IN ({listed})"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    _replace(OLD_CATEGORIES)
    op.execute("DROP TABLE public.swedebeat_analyses")
