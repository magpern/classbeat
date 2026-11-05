"""The job's mix-analysis slot (issue #574, ADR 0005 section 3)

Revision ID: 0032_analysis_slot
Revises: 0031_oauth_sign_up_purpose
Create Date: 2026-11-05

``jobs.analysis_slot``: the job needs one of the worker's mix-analysis slots
(``CLASSBEAT_MIX_ANALYSIS_CONCURRENCY``). ``queue_claim`` claims a queued job that has it only
while fewer running jobs have it than the cap, so a burst of mix analyses never fills every worker
slot. The worker sets it when a run finds it must analyse a mix and has no slot (the job is
requeued with it, the attempt not counted, and a ``requeued`` event with the reason
``analysis_slot``), and clears it in the transaction that stores the analysis. Every existing
job starts without it.

The queue functions read the column, so ``docker/postgres/queue/20-queue.sql`` goes to
``classbeat-queue v3`` in the same PR. No index: the claim counts the running jobs through
``jobs_running`` (migration 0004).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032_analysis_slot"
down_revision: str | Sequence[str] | None = "0031_oauth_sign_up_purpose"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.jobs ADD COLUMN analysis_slot boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE public.jobs DROP COLUMN analysis_slot")
