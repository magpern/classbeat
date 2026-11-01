"""Job categories of the Swedebeat mix fetch and analysis (ADR 0016 sections 3 and 4; A3)

Revision ID: 0019_swedebeat_media_categories
Revises: 0018_swedebeat_mix_maps
Create Date: 2026-11-01

``jobs.error_category`` gains three categories, all of them not retried:

- ``media_host_not_allowed``: the temporary link points at a host the media list does not hold
  (today the list is empty, so every uncached mix ends so);
- ``media_refused``: the media answer was refused (a status, a redirect, a compressed or too large
  body, a date past the limits);
- ``analysis_failed``: the audio could not be decoded or analysed within its limits (an
  undecodable or too long file, a crash, the memory or time cap of the child process).

Only ``jobs.error_category`` holds them (a music step runs before any model call, so no
``agent_runs`` row and no external operation carries them).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_swedebeat_media_categories"
down_revision: str | Sequence[str] | None = "0018_swedebeat_mix_maps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0011.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs"
)
NEW = ("media_host_not_allowed", "media_refused", "analysis_failed")
CATEGORIES = f"{OLD_CATEGORIES}, {', '.join(repr(name) for name in NEW)}"


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category")
    op.execute(
        f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({values}))"
    )


def upgrade() -> None:
    _replace(CATEGORIES)


def downgrade() -> None:
    # The owner is exempt from row security while it is not forced (as in 0011).
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    listed = ", ".join(repr(name) for name in NEW)
    op.execute(
        "UPDATE public.jobs SET error_category = 'internal' "
        f"WHERE error_category IN ({listed})"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    _replace(OLD_CATEGORIES)
