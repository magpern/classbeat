"""Retire the ``soundless_enabled`` admin flag (ADR 0007 amendment of 2026-11-08; #824)

Revision ID: 0043_retire_soundless_flag
Revises: 0042_plan_save_caps
Create Date: 2026-11-08

Soundless classes are on when ``CLASSBEAT_SOUNDLESS_ENABLED`` is on and every plan's ``design``
route covers soundless; no stored admin value takes part any more, and nothing reads the key.
The upgrade deletes a stored ``soundless_enabled`` row (``on``, ``off`` or the cleared JSON
``null``) and narrows the ``app_settings.key`` check (migrations 0007, 0016, 0025, 0027 and 0035)
so the key cannot be stored again. The downgrade widens the check back; it restores no row, and
the older code reads a missing row as off, its stricter default.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043_retire_soundless_flag"
down_revision: str | Sequence[str] | None = "0042_plan_save_caps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAG_KEYS = "'signup_mode', 'song_picking', 'swedebeat_enabled', 'running_enabled'"
REST_KEYS = (
    "'trial', 'banner', "
    "'mail_sender_name', 'mail_support_email', 'mail_footer', 'mail_kinds_disabled'"
)
OLD_KEYS = f"{FLAG_KEYS}, 'soundless_enabled', {REST_KEYS}"
NEW_KEYS = f"{FLAG_KEYS}, {REST_KEYS}"
_DROP = "ALTER TABLE public.app_settings DROP CONSTRAINT app_settings_key"
_ADD = "ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_key CHECK (key IN ({keys}))"


def upgrade() -> None:
    # A stored soundless_enabled row would violate the narrowed check: remove it first.
    op.execute("DELETE FROM public.app_settings WHERE key = 'soundless_enabled'")
    op.execute(_DROP)
    op.execute(_ADD.format(keys=NEW_KEYS))


def downgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(keys=OLD_KEYS))
