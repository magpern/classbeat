"""spotdown_song_budgets: ``fallback`` is ``unavailable`` (ADR 0040 amendment; #1299)

Revision ID: 0079_song_budget_unavailable
Revises: 0078_spotify_offset_corrections
Create Date: 2026-11-23

spotdown is the only analysis path (ADR 0040 amendment "spotdown is the only analysis path"):
there is no fallback to today's music or MOWL's sections. The flag that 0066 called ``fallback``
only records that spotdown was found unreachable or in a provider outage during a run of the job,
so every later run of the same attempt ends ``analysis_unavailable`` without calling it again
(``classbeat.songs.pick_match_budget``); it is renamed for what it is. Nothing else changes: the
type, default and rows stay (a rename keeps them), and the job reset still clears it per attempt.

The downgrade renames it back.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0079_song_budget_unavailable"
down_revision: str | Sequence[str] | None = "0078_spotify_offset_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "public.spotdown_song_budgets"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} RENAME COLUMN fallback TO unavailable")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} RENAME COLUMN unavailable TO fallback")
