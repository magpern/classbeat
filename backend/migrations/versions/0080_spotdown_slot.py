"""jobs: ``analysis_slot`` is ``spotdown_slot`` (ADR 0040 amendment; #1299)

Revision ID: 0080_spotdown_slot
Revises: 0079_song_budget_unavailable
Create Date: 2026-11-23

spotdown is the only analysis path (ADR 0040 amendment "spotdown is the only analysis path"). The
flag that 0032 called ``analysis_slot`` marks a job that holds, or waits for, one of the slots
that cap concurrent spotdown mix analyses (``CLASSBEAT_SPOTDOWN_MIX_CONCURRENCY``); it is renamed
for what it is. Nothing else changes: the type, the default and the rows stay (a rename keeps
them), and no index or constraint names it.

The queue functions read the column, so ``docker/postgres/queue/20-queue.sql`` goes to
``classbeat-queue v6`` in the same PR (``queue_claim``'s parameter is ``p_spotdown_slots`` and its
advisory lock key follows the name). The lock key changes safely: the rename takes an ACCESS
EXCLUSIVE lock on ``jobs``, so no claim of the v5 function (which reads the old column) runs
after it commits, and a claim of v6 runs only after it. Until ``db-functions`` installs v6 the
worker's claims fail, as on every queue version bump; ``up`` runs it right after ``migrate``.

The downgrade renames it back; pair it with reinstalling ``classbeat-queue v5`` (the queue SQL of
the commit before this one), whose ``queue_claim`` reads ``analysis_slot``. v5 uses ``CREATE OR
REPLACE``, which cannot rename v6's ``p_spotdown_slots`` parameter back, so drop v6's function
first (``DROP FUNCTION public.queue_claim(text, integer, text[], integer)``) and then run the v5
script, in one transaction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0080_spotdown_slot"
down_revision: str | Sequence[str] | None = "0079_song_budget_unavailable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "public.jobs"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} RENAME COLUMN analysis_slot TO spotdown_slot")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} RENAME COLUMN spotdown_slot TO analysis_slot")
