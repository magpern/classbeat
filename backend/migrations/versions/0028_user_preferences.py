"""``user_preferences``: the per-user "I use Swedebeat" preference (issue #525, ADR 0015 amendment)

Revision ID: 0028_user_preferences
Revises: 0027_soundless_flag
Create Date: 2026-11-03

One row per user at most (``UNIQUE (owner_id)``), owner RLS, gone with the account. No row means
every preference at its default, so a user who never opened the switch has no row and nothing is
written for them. ``uses_swedebeat`` is false by default: the Swedebeat card, the "Use my Swedebeat
mix" choice, the mixes listing and the Swedebeat connect stay hidden or refused until the user turns
it on, and only while the effective ``swedebeat_enabled`` flag is on too (the flag is the kill
switch, stricter wins). The table holds booleans and dates only, nothing provider-derived.

No backfill: the migration role is subject to the forced row security of ``connections`` and the
Swedebeat flag is off in every deployment so far; a user who already holds a Swedebeat connection
still sees its status and Disconnect, and turns the preference on with one switch.

The application role gets ``SELECT, INSERT, UPDATE, DELETE`` from the owner's default privileges
(``10-roles.sql``); ``classbeat_maint`` gets nothing (guard 11).
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0028_user_preferences"
down_revision: str | Sequence[str] | None = "0027_soundless_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_PREFERENCES = """
    CREATE TABLE public.user_preferences (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        uses_swedebeat boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT user_preferences_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT user_preferences_owner_id_key UNIQUE (owner_id)
    )"""


def upgrade() -> None:
    op.execute(USER_PREFERENCES)
    enable_owner_rls("user_preferences")


def downgrade() -> None:
    op.execute("DROP TABLE public.user_preferences")
