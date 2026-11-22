"""The pin_unconfirmed job category (ADR 0042 section 5; #1390)

Revision ID: 0073_pin_unconfirmed
Revises: 0072_preview_clips
Create Date: 2026-11-22

``jobs.error_category`` gains ``pin_unconfirmed``: ``produce_outputs`` refuses again, when the job
runs, an output chosen while a confirmed cut pin's music changed and it is neither re-confirmed nor
cleared; nothing is written and it is not retried. Without it ``JobFailure`` would turn the
category into ``internal`` (``classbeat.jobs.errors.CATEGORIES`` mirrors this check). ``jobs`` is
the only table that lists the job categories.

The downgrade turns the new category into ``internal`` with row security lifted for that
statement, as 0071 does, and restores 0071's check.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0073_pin_unconfirmed"
down_revision: str | Sequence[str] | None = "0072_preview_clips"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0071 (the last one to change it; 0072 leaves it as it is).
OLD_JOB_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed feature_off analysis_limit handoff_expired program_other_sport "
    "music_changed player_ahead player_conflict track_too_large track_unavailable track_limit "
    "connection_changed writes_off render_not_reproducible output_limit playlist_has_repeats "
    "analysis_unavailable mix_too_long mix_not_chosen mix_not_applied"
)
NEW_JOB_CATEGORIES = _quoted("pin_unconfirmed")
# The jobs check from here on (``classbeat.jobs.errors.CATEGORIES``, ``tests/test_jobs_errors.py``).
CATEGORIES = f"{OLD_JOB_CATEGORIES}, {NEW_JOB_CATEGORIES}"


def _check(categories: str) -> list[str]:
    return [
        "ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category",
        f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({categories}))",
    ]


def upgrade() -> None:
    for statement in _check(CATEGORIES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE public.jobs SET error_category = 'internal' "
        f"WHERE error_category IN ({NEW_JOB_CATEGORIES})"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    for statement in _check(OLD_JOB_CATEGORIES):
        op.execute(statement)
