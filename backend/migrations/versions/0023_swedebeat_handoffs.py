"""The Swedebeat manual hand-off (ADR 0016 section 7, B2)

Revision ID: 0023_swedebeat_handoffs
Revises: 0022_swedebeat_approval
Create Date: 2026-11-02

- ``swedebeat_handoffs``: one live row per class (the partial unique index), owner RLS, gone with
  the class and the account. The application also deletes it on Disconnect MOWL and a MOWL account
  change, and the hourly purge deletes rows expired for 7 days. ``match_revision_id`` can only
  name a revision of the same class. Whether that revision is the class's current song list is
  the application's check (a stored id of the same owner is not enough).
- ``jobs.error_category`` gains ``handoff_expired``: a verify job whose row expired before
  anything was attached or fetched. It is not retried.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0023_swedebeat_handoffs"
down_revision: str | Sequence[str] | None = "0022_swedebeat_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _quoted(names: str) -> str:
    return ", ".join(f"'{name}'" for name in names.split())


# The jobs check of migration 0020, plus this category.
OLD_CATEGORIES = _quoted(
    "upstream_unavailable rate_limited model_unavailable lease_lost operation_busy not_connected "
    "needs_reconnect key_unavailable decrypt_failed invalid_input account_disabled model_refused "
    "model_config invalid_output limit_exceeded uncertain_write outbound_blocked internal "
    "program_changed program_missing music_unlinked invalid_grant app_misconfigured "
    "not_allowlisted forbidden quota_exhausted not_found scope_mismatch invalid_response "
    "account_changed access_denied consent_missing pool_unusable pool_missing pool_not_synced "
    "playlist_mismatch pool_too_small use_change_songs media_host_not_allowed media_refused "
    "analysis_failed feature_off analysis_limit"
)
CATEGORIES = f"{OLD_CATEGORIES}, 'handoff_expired'"

SWEDEBEAT_HANDOFFS = """
    CREATE TABLE public.swedebeat_handoffs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        match_revision_id uuid NOT NULL,
        state text NOT NULL
            CONSTRAINT swedebeat_handoffs_state CHECK (state IN ('open', 'checked', 'verified')),
        mixes_before jsonb NOT NULL
            CONSTRAINT swedebeat_handoffs_mixes_before CHECK (
                jsonb_typeof(mixes_before) = 'array'
                AND octet_length(mixes_before::text) <= 65536
            ),
        mix_id text
            CONSTRAINT swedebeat_handoffs_mix_id CHECK (
                mix_id IS NULL OR mix_id ~ '^[0-9A-Za-z_-]{1,64}$'
            ),
        verdict text
            CONSTRAINT swedebeat_handoffs_verdict CHECK (
                verdict IS NULL OR verdict IN ('ok', 'too_short', 'too_long', 'not_found')
            ),
        detail jsonb
            CONSTRAINT swedebeat_handoffs_detail CHECK (
                detail IS NULL OR octet_length(detail::text) <= 65536
            ),
        checked_at timestamptz,
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        superseded_at timestamptz,
        CONSTRAINT swedebeat_handoffs_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT swedebeat_handoffs_verified_mix CHECK (
            state <> 'verified' OR mix_id IS NOT NULL
        ),
        CONSTRAINT swedebeat_handoffs_verdict_mix CHECK (
            verdict IS NULL OR verdict = 'not_found' OR mix_id IS NOT NULL
        ),
        CONSTRAINT swedebeat_handoffs_not_found_mix CHECK (
            verdict IS DISTINCT FROM 'not_found' OR mix_id IS NULL
        ),
        CONSTRAINT swedebeat_handoffs_lifetime CHECK (
            expires_at > created_at AND expires_at <= created_at + interval '7 days'
        ),
        CONSTRAINT swedebeat_handoffs_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT swedebeat_handoffs_match_fkey
            FOREIGN KEY (owner_id, class_id, match_revision_id)
            REFERENCES public.class_revisions (owner_id, class_id, id) ON DELETE CASCADE
    )"""


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_error_category")
    op.execute(
        "ALTER TABLE public.jobs ADD CONSTRAINT jobs_error_category "
        f"CHECK (error_category IN ({values}))"
    )


def upgrade() -> None:
    op.execute(SWEDEBEAT_HANDOFFS)
    enable_owner_rls("swedebeat_handoffs")
    op.execute(
        "CREATE UNIQUE INDEX swedebeat_handoffs_one_live ON public.swedebeat_handoffs "
        "(owner_id, class_id) WHERE superseded_at IS NULL"
    )
    _replace(CATEGORIES)


def downgrade() -> None:
    op.execute("DROP TABLE public.swedebeat_handoffs")
    op.execute("ALTER TABLE public.jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE public.jobs SET error_category = 'internal' "
        "WHERE error_category = 'handoff_expired'"
    )
    op.execute("ALTER TABLE public.jobs FORCE ROW LEVEL SECURITY")
    _replace(OLD_CATEGORIES)
