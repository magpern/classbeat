"""The ``swedebeat_enabled`` flag (ADR 0015 section 6; #362)

Revision ID: 0016_swedebeat_flag
Revises: 0015_gdpr_events_cascade
Create Date: 2026-10-31

``app_settings.key`` is checked against the flag keys (migration 0007). Swedebeat becomes a real
admin flag, so its key joins the list. Nothing else changes: ``connections`` already allows the
``swedebeat`` provider, and the connect failures of ADR 0015 (``device_in_use``, the app key being
unavailable) are never stored in ``connections.last_error``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_swedebeat_flag"
down_revision: str | Sequence[str] | None = "0015_gdpr_events_cascade"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEYS = "'signup_mode', 'song_picking', 'trial', 'banner'"
NEW_KEYS = "'signup_mode', 'song_picking', 'swedebeat_enabled', 'trial', 'banner'"
_DROP = "ALTER TABLE public.app_settings DROP CONSTRAINT app_settings_key"
_ADD = "ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_key CHECK (key IN ({keys}))"


def upgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(keys=NEW_KEYS))


def downgrade() -> None:
    # A stored swedebeat_enabled row would violate the old check: remove it first.
    op.execute("DELETE FROM public.app_settings WHERE key = 'swedebeat_enabled'")
    op.execute(_DROP)
    op.execute(_ADD.format(keys=OLD_KEYS))
