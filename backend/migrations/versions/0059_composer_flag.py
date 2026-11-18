"""The ``composer_flow`` flag (ADR 0038 section 2; #1185)

Revision ID: 0059_composer_flag
Revises: 0058_discarded_first_creation
Create Date: 2026-11-17

Widens the ``app_settings.key`` check (migrations 0007, 0016, 0025, 0027, 0035 and 0043) so an
admin can store ``composer_flow`` (``off`` or ``on``; only a stored ``on`` turns the composer
flow on, under ``CLASSBEAT_COMPOSER_FLOW``). No row is written. The downgrade deletes a stored
``composer_flow`` row first, then narrows the check back; the older code has no such key.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0059_composer_flag"
down_revision: str | Sequence[str] | None = "0058_discarded_first_creation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAG_KEYS = "'signup_mode', 'song_picking', 'swedebeat_enabled', 'running_enabled'"
REST_KEYS = (
    "'trial', 'banner', "
    "'mail_sender_name', 'mail_support_email', 'mail_footer', 'mail_kinds_disabled'"
)
OLD_KEYS = f"{FLAG_KEYS}, {REST_KEYS}"
NEW_KEYS = f"{FLAG_KEYS}, 'composer_flow', {REST_KEYS}"
_DROP = "ALTER TABLE public.app_settings DROP CONSTRAINT app_settings_key"
_ADD = "ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_key CHECK (key IN ({keys}))"


def upgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(keys=NEW_KEYS))


def downgrade() -> None:
    # A stored composer_flow row would violate the narrowed check: remove it first.
    op.execute("DELETE FROM public.app_settings WHERE key = 'composer_flow'")
    op.execute(_DROP)
    op.execute(_ADD.format(keys=OLD_KEYS))
