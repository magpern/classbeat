"""The job category ``use_change_songs`` (ADR 0008 section 6, ADR 0009 section 8; issue #278)

Revision ID: 0011_use_change_songs
Revises: 0010_m5_hardening
Create Date: 2026-10-31

A ``revise_draft`` request that only asks for other songs ends the job with this category: no
revision is stored and the class page points to "Change songs". Only ``jobs.error_category``
holds it (the stage run itself ends ``submitted``); the other category checks are unchanged.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_use_change_songs"
down_revision: str | Sequence[str] | None = "0010_m5_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0006.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small"
)
CATEGORIES = f"{OLD_CATEGORIES}, 'use_change_songs'"


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category")
    op.execute(
        f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({values}))"
    )


def upgrade() -> None:
    _replace(CATEGORIES)


def downgrade() -> None:
    # The owner is exempt from row security while it is not forced (as in 0005 and 0006).
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE public.jobs SET error_category = 'internal' WHERE error_category = 'use_change_songs'"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    _replace(OLD_CATEGORIES)
