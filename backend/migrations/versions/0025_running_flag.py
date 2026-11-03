"""The ``running_enabled`` flag (ADR 0018 section 5; #463)

Revision ID: 0025_running_flag
Revises: 0024_program_other_sport
Create Date: 2026-11-03

``app_settings.key`` is checked against the flag keys (migrations 0007 and 0016). Running classes
get an admin flag copied from ``swedebeat_enabled``, so its key joins the list. Nothing else
changes: a class's sport is a key of its ``brief`` (JSON, ADR 0018 section 1), so no column is
added, and a running job refused because the flag is off ends with the existing ``feature_off``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_running_flag"
down_revision: str | Sequence[str] | None = "0024_program_other_sport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEYS = "'signup_mode', 'song_picking', 'swedebeat_enabled', 'trial', 'banner'"
NEW_KEYS = "'signup_mode', 'song_picking', 'swedebeat_enabled', 'running_enabled', 'trial', 'banner'"
_DROP = "ALTER TABLE public.app_settings DROP CONSTRAINT app_settings_key"
_ADD = "ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_key CHECK (key IN ({keys}))"


def upgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(keys=NEW_KEYS))


def downgrade() -> None:
    # A stored running_enabled row would violate the old check: remove it first.
    op.execute("DELETE FROM public.app_settings WHERE key = 'running_enabled'")
    op.execute(_DROP)
    op.execute(_ADD.format(keys=OLD_KEYS))
