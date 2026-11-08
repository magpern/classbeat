"""Email preferences on ``users`` (ADR 0022 section 5; ME4, #655)

Revision ID: 0036_mail_preferences
Revises: 0035_mail_settings
Create Date: 2026-11-06

``users.mail_unsubscribed_all`` (no optional mail at all) and ``users.mail_opted_out`` (the
categories switched off) default to "send everything", so no backfill is needed. The check keeps
``mail_opted_out`` to the categories a user may switch off: ``essential`` is not among them, so no
preference, and no unsubscribe link, can ever silence sign-in, reset or confirmation mail. Both
columns go with the account (they are on ``users``); the export lists them (guard 12).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0036_mail_preferences"
down_revision: str | Sequence[str] | None = "0035_mail_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.users "
        "ADD COLUMN mail_unsubscribed_all boolean NOT NULL DEFAULT false, "
        "ADD COLUMN mail_opted_out text[] NOT NULL DEFAULT '{}'::text[], "
        "ADD CONSTRAINT users_mail_opted_out_check CHECK "
        "(mail_opted_out <@ ARRAY['welcome', 'account_activity', 'class_ready']::text[])"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.users DROP CONSTRAINT users_mail_opted_out_check, "
        "DROP COLUMN mail_opted_out, DROP COLUMN mail_unsubscribed_all"
    )
