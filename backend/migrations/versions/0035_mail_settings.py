"""The mail settings keys (ADR 0022 sections 2 and 6; #653)

Revision ID: 0035_mail_settings
Revises: 0034_security_log
Create Date: 2026-11-06

``app_settings.key`` is checked against the setting keys (migrations 0007, 0016, 0025 and 0027).
The admin Mail page stores the branding (``mail_sender_name``, ``mail_support_email``,
``mail_footer``) and the switched-off kinds (``mail_kinds_disabled``) there, so the four keys join
the list. Nothing else changes. The downgrade removes any stored mail row first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035_mail_settings"
down_revision: str | Sequence[str] | None = "0034_security_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEYS = (
    "'signup_mode', 'song_picking', 'swedebeat_enabled', 'running_enabled', "
    "'soundless_enabled', 'trial', 'banner'"
)
MAIL_KEYS = (
    "'mail_sender_name', 'mail_support_email', 'mail_footer', 'mail_kinds_disabled'"
)
NEW_KEYS = f"{OLD_KEYS}, {MAIL_KEYS}"
_DROP = "ALTER TABLE public.app_settings DROP CONSTRAINT app_settings_key"
_ADD = "ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_key CHECK (key IN ({keys}))"


def upgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(keys=NEW_KEYS))


def downgrade() -> None:
    # A stored mail row would violate the old check: remove it first.
    op.execute(f"DELETE FROM public.app_settings WHERE key IN ({MAIL_KEYS})")
    op.execute(_DROP)
    op.execute(_ADD.format(keys=OLD_KEYS))
