"""The job category ``program_other_sport`` (issue #485, ADR 0007 section 7, ADR 0018)

Revision ID: 0024_program_other_sport
Revises: 0023_swedebeat_handoffs
Create Date: 2026-11-03

An ``update_workout`` whose program in MOWL is of another sport than the class (its own
``ActivityTypeID``), or of one classbeat cannot tell, ends the job with this category before
anything is written, ``overwrite`` included. Only ``jobs.error_category`` holds it: the refusal
is raised before any operation row is written (or inside the guard, whose transaction rolls back),
so ``external_operations`` and ``agent_runs`` never carry it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024_program_other_sport"
down_revision: str | Sequence[str] | None = "0023_swedebeat_handoffs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0023.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed feature_off analysis_limit handoff_expired"
)
CATEGORIES = f"{OLD_CATEGORIES}, 'program_other_sport'"


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category")
    op.execute(
        "ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({values}))"
    )


def upgrade() -> None:
    _replace(CATEGORIES)


def downgrade() -> None:
    # The owner is exempt from row security while it is not forced (as in 0011 and 0023).
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE public.jobs SET error_category = 'internal' "
        "WHERE error_category = 'program_other_sport'"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    _replace(OLD_CATEGORIES)
