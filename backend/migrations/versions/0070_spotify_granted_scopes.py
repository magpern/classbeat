"""Spotify: the scopes a connection was granted (ADR 0042 section 7.1, amends ADR 0009 section 1;
#1393)

Revision ID: 0070_spotify_granted_scopes
Revises: 0069_mix_too_long
Create Date: 2026-11-21

- ``connections.granted_scopes``: the scopes of the Spotify token answer that classbeat knows
  (``classbeat.spotify.flow.KNOWN_SCOPES``), sorted and space-separated
  (``classbeat.spotify.flow.stored_scopes``), written on every Spotify connect and reconnect under
  the row lock, so a reconnect without the listening-preview opt-in drops them. Only ``spotify``
  rows may hold one. ``NULL`` is a row from before this migration and reads as "no
  listening-preview scopes" (``preview_granted`` is false); the connection itself works as before.
  It names classbeat's own scope vocabulary, nothing of the user's account, and lives and goes with
  its row (owner RLS already forced on ``connections``; ``classbeat_maint``'s column grants do not
  include it).

The downgrade drops the column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0070_spotify_granted_scopes"
down_revision: str | Sequence[str] | None = "0069_mix_too_long"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.connections ADD COLUMN granted_scopes text "
        "CONSTRAINT connections_granted_scopes CHECK (granted_scopes IS NULL OR "
        "(provider = 'spotify' AND granted_scopes ~ '^[a-z-]+( [a-z-]+)*$'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.connections DROP COLUMN granted_scopes")
