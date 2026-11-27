"""Retire the ``running_enabled`` admin flag (ADR 0018 Amendment 6; #1544)

Revision ID: 0082_retire_running_flag
Revises: 0081_drop_map_analyser
Create Date: 2026-11-27

Running classes are always available (owner, 2026-11-26): no environment switch and no stored
admin value take part any more, and nothing reads the key. The upgrade deletes a stored
``running_enabled`` row (``on``, ``off`` or the cleared JSON ``null``) and narrows the
``app_settings.key`` check (migrations 0007, 0016, 0025, 0027, 0035, 0043 and 0059) so the key
cannot be stored again, as 0043 did for ``soundless_enabled``. The downgrade widens the check
back; it restores no row, and the older code reads a missing row as off, its stricter default.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0082_retire_running_flag"
down_revision: str | Sequence[str] | None = "0081_drop_map_analyser"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAG_KEYS = "'signup_mode', 'song_picking', 'swedebeat_enabled'"
REST_KEYS = (
    "'composer_flow', 'trial', 'banner', "
    "'mail_sender_name', 'mail_support_email', 'mail_footer', 'mail_kinds_disabled'"
)
OLD_KEYS = f"{FLAG_KEYS}, 'running_enabled', {REST_KEYS}"
NEW_KEYS = f"{FLAG_KEYS}, {REST_KEYS}"
_DROP = "ALTER TABLE public.app_settings DROP CONSTRAINT app_settings_key"
_ADD = "ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_key CHECK (key IN ({keys}))"


def upgrade() -> None:
    # A stored running_enabled row would violate the narrowed check: remove it first.
    op.execute("DELETE FROM public.app_settings WHERE key = 'running_enabled'")
    op.execute(_DROP)
    op.execute(_ADD.format(keys=NEW_KEYS))


def downgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(keys=OLD_KEYS))
