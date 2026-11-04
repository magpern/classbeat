"""The ``sign_up`` purpose of an OAuth flow (issue #617, ADR 0003 section 5 amendment 2026-11-04)

Revision ID: 0031_oauth_sign_up_purpose
Revises: 0030_plan_price_privilege
Create Date: 2026-11-04

Until now the sign-in and the sign-up screen both started a ``sign_in`` flow, so the callback
could not tell which screen a new identity came from and answered ``invite_required`` on both.
``oauth_flows.purpose`` gains ``sign_up``: only a ``sign_up`` flow may create an account (and
carry an invite); a ``sign_in`` flow with a new identity ends ``no_account`` and creates nothing.

Flows live at most 10 minutes and are deleted on use, so the downgrade deletes any ``sign_up``
flow still waiting (its callback then finds no flow, the generic failure) before the check
narrows again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031_oauth_sign_up_purpose"
down_revision: str | Sequence[str] | None = "0030_plan_price_privilege"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PURPOSES = "'sign_in', 'link', 'reauth'"  # migration 0002
NEW_PURPOSES = "'sign_in', 'sign_up', 'link', 'reauth'"


def _replace(values: str) -> None:
    op.execute("ALTER TABLE public.oauth_flows DROP CONSTRAINT oauth_flows_purpose")
    op.execute(
        "ALTER TABLE public.oauth_flows ADD CONSTRAINT oauth_flows_purpose "
        f"CHECK (purpose IN ({values}))"
    )


def upgrade() -> None:
    _replace(NEW_PURPOSES)


def downgrade() -> None:
    op.execute("DELETE FROM public.oauth_flows WHERE purpose = 'sign_up'")
    _replace(OLD_PURPOSES)
