"""A studio class that repeats a song is mix-only (ADR 0036 amendment A8; #1342)

Revision ID: 0067_playlist_has_repeats
Revises: 0065_mix_map_provenance
Create Date: 2026-11-21

``jobs.error_category`` gains ``playlist_has_repeats``: a ``produce_outputs`` job that names
``swedebeat_playlist`` for a draft whose mix spec plays a song in more than one piece, refused
again when the job runs (``outputs_admit.bound``); nothing is written. Without it ``JobFailure``
would turn the category into ``internal`` (``classbeat.jobs.errors.CATEGORIES`` mirrors this check).

Numbered 0067 because 0066 is taken (it sits before 0064 in the chain); it revises 0065, the head on
``main`` now.

The downgrade turns the new category into ``internal`` with row security lifted for that statement,
as 0054 does, and restores 0054's check.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0067_playlist_has_repeats"
down_revision: str | Sequence[str] | None = "0065_mix_map_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0054 (the last one to change it).
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
    "connection_changed writes_off render_not_reproducible output_limit"
)
NEW_JOB_CATEGORIES = _quoted("playlist_has_repeats")
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
