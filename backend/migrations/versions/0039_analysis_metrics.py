"""Analysis timings for the stage 1 measurement of ADR 0026 (#735)

Revision ID: 0039_analysis_metrics
Revises: 0038_locale
Create Date: 2026-11-07

``jobs.analysis_fetch_ms`` and ``jobs.analysis_decode_ms``: how long the job's fresh Swedebeat mix
analysis took to fetch the audio and to decode and analyse it, in whole milliseconds, and
``jobs.analysed_at``, when it was stored. All three are set together, in the fenced transaction
that stores the analysis (``classbeat.design.swedebeat_music``), so a job that found the analysis
cached, or whose analysis failed, has none. ``queue_stats()`` (``docker/postgres/queue/20-queue.sql``,
``classbeat-queue v5``) reads them for the p95 of the last seven days; the queue wait comes from the
``claimed`` events, not from here. Counts and a time only: no id, title or link.

The check keeps the three together and non-negative. Not exported (internal bookkeeping, like
``analysis_slot``).

Downgrade: drops the columns. Pair it with reinstalling ``classbeat-queue v4`` (the queue SQL of
the commit before #735): until then ``queue_stats()`` fails and the queue check refuses the
database, whose functions no longer match a shipped version.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_analysis_metrics"
down_revision: str | Sequence[str] | None = "0038_locale"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.jobs ADD COLUMN analysis_fetch_ms integer, "
        "ADD COLUMN analysis_decode_ms integer, ADD COLUMN analysed_at timestamptz, "
        "ADD CONSTRAINT jobs_analysis_metrics CHECK ("
        "(analysed_at IS NULL AND analysis_fetch_ms IS NULL AND analysis_decode_ms IS NULL) "
        "OR (analysed_at IS NOT NULL AND analysis_fetch_ms IS NOT NULL "
        "AND analysis_decode_ms IS NOT NULL AND analysis_fetch_ms >= 0 "
        "AND analysis_decode_ms >= 0))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.jobs DROP CONSTRAINT jobs_analysis_metrics, "
        "DROP COLUMN analysed_at, DROP COLUMN analysis_decode_ms, DROP COLUMN analysis_fetch_ms"
    )
