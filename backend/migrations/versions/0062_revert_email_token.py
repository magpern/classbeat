"""The ``revert_email`` mail token (issue #1265, ADR 0003 amendment of 2026-11-19)

Revision ID: 0062_revert_email_token
Revises: 0061_composer_save
Create Date: 2026-11-19

A confirmed email change mails the old address a "this wasn't me" link. Its token is an
``auth_tokens`` row like the others (only the SHA-256 stored, single use) with purpose
``revert_email``, a 7-day lifetime, ``new_email`` the old address to restore and
``email_at_issue`` the new address the account must still have. No new column and no new grant:
the table already holds both addresses, and the application role already has its DML.

The downgrade deletes every ``revert_email`` row (the old addresses lose their way back, which
is what the code before this migration offered) before the check narrows again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0062_revert_email_token"
down_revision: str | Sequence[str] | None = "0061_composer_save"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PURPOSES = "'reset_password', 'change_email'"  # migration 0002
NEW_PURPOSES = "'reset_password', 'change_email', 'revert_email'"


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.auth_tokens DROP CONSTRAINT auth_tokens_purpose")
    op.execute(
        "ALTER TABLE public.auth_tokens ADD CONSTRAINT auth_tokens_purpose "
        f"CHECK (purpose IN ({values}))"
    )


def upgrade() -> None:
    _replace(NEW_PURPOSES)


def downgrade() -> None:
    op.execute("DELETE FROM public.auth_tokens WHERE purpose = 'revert_email'")
    _replace(OLD_PURPOSES)
