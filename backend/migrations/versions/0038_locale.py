"""Language preference (ADR 0023 sections 1, 2 and 9; ML2, #664)

Revision ID: 0038_locale
Revises: 0037_mail_handoff
Create Date: 2026-11-06

``users.locale`` is the account's language (``NULL``: follow the browser, English mail; every
account that exists when this migrates). ``pending_signups.locale`` and ``oauth_flows.locale``
carry the sign-up language to the new account (the flow row is the only place an OAuth start can
leave it: the callback is a redirect with no body). All three are nullable ``text`` with the same
format check (two lower-case letters); which codes are offered is the application's allowlist, not
the database's, so a language is enabled without a migration. ``pending_signups`` and
``oauth_flows`` stay unexported short-lived rows; ``users.locale`` is in the export (guard 12).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038_locale"
down_revision: str | Sequence[str] | None = "0037_mail_handoff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("users", "pending_signups", "oauth_flows")


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"ALTER TABLE public.{table} ADD COLUMN locale text, "
            f"ADD CONSTRAINT {table}_locale_check CHECK (locale ~ '^[a-z]{{2}}$')"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(
            f"ALTER TABLE public.{table} DROP CONSTRAINT {table}_locale_check, DROP COLUMN locale"
        )
