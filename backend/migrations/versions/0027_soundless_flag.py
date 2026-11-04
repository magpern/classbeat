"""The ``soundless_enabled`` flag (ADR 0007 amendment of 2026-11-03, soundless classes; #527)

Revision ID: 0027_soundless_flag
Revises: 0026_plan_gates
Create Date: 2026-11-03

``app_settings.key`` is checked against the flag keys (migrations 0007, 0016 and 0025). Soundless
classes get an admin flag, so its key joins the list. Nothing else changes: a soundless class's
music is ``{"source": "none"}`` in the existing ``classes.music_source`` and
``class_revisions.music`` (both ``jsonb``), so no column is added.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_soundless_flag"
down_revision: str | Sequence[str] | None = "0026_plan_gates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEYS = "'signup_mode', 'song_picking', 'swedebeat_enabled', 'running_enabled', 'trial', 'banner'"
NEW_KEYS = (
    "'signup_mode', 'song_picking', 'swedebeat_enabled', 'running_enabled', "
    "'soundless_enabled', 'trial', 'banner'"
)
_DROP = "ALTER TABLE public.app_settings DROP CONSTRAINT app_settings_key"
_ADD = "ALTER TABLE public.app_settings ADD CONSTRAINT app_settings_key CHECK (key IN ({keys}))"


def upgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(keys=NEW_KEYS))


def downgrade() -> None:
    # A stored soundless_enabled row would violate the old check: remove it first.
    op.execute("DELETE FROM public.app_settings WHERE key = 'soundless_enabled'")
    op.execute(_DROP)
    op.execute(_ADD.format(keys=OLD_KEYS))
