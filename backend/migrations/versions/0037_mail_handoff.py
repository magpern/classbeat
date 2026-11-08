"""The worker's hand-off of "class ready" mail to the web process (ADR 0022 section 4; ME5, #656)

Revision ID: 0037_mail_handoff
Revises: 0036_mail_preferences
Create Date: 2026-11-06

``mail_handoff``: one row per mail the worker owes and the web process sends (the worker never
sends mail). The worker inserts it in the fenced transaction that finishes a job which ran a fresh
mix analysis; the web process takes it with the ``SECURITY DEFINER`` function
``mail_handoff_take`` (``docker/postgres/queue/20-queue.sql``, ``classbeat-queue v4``), which
deletes what it returns and drops rows older than a day. ``params`` holds the class id only (the
check below refuses anything else), so the row carries no address, title or token.

Owned (owner RLS, ADR 0002) and owner-inclusive: ``UNIQUE (owner_id, kind, job_id)`` is the
plan's ``(kind, job_id)`` (a job has one owner), so a retried or stale attempt adds nothing; the
job's foreign key cascades, so the row goes with its job, its class (``jobs`` cascade from
``classes``) and the account (``users``).

``jobs.fresh_analysis``: the job ran a fresh mix analysis in one of its attempts, set in the fenced
transaction that stores the analysis, so the attempt that finishes the job knows it even when a
retry found the analysis cached. The queue functions do not read it.

Downgrade: drops the table and the column. Pair it with reinstalling ``classbeat-queue v3`` (the
queue SQL of the commit before #656): until then ``mail_handoff_take`` fails and the queue check
refuses the database, whose functions no longer match a shipped version.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0037_mail_handoff"
down_revision: str | Sequence[str] | None = "0036_mail_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAIL_HANDOFF = """
    CREATE TABLE public.mail_handoff (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        kind text NOT NULL,
        job_id uuid NOT NULL,
        params jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT mail_handoff_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT mail_handoff_owner_id_kind_job_id_key UNIQUE (owner_id, kind, job_id),
        CONSTRAINT mail_handoff_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT mail_handoff_kind CHECK (kind IN ('class_ready')),
        CONSTRAINT mail_handoff_params CHECK (
            jsonb_typeof(params) = 'object'
            AND params ? 'class_id'
            AND jsonb_typeof(params -> 'class_id') = 'string'
            AND params - 'class_id' = '{}'::jsonb
        )
    )"""


def upgrade() -> None:
    op.execute(MAIL_HANDOFF)
    enable_owner_rls("mail_handoff")
    op.execute("CREATE INDEX mail_handoff_created_at ON public.mail_handoff (created_at)")
    op.execute("ALTER TABLE public.jobs ADD COLUMN fresh_analysis boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE public.jobs DROP COLUMN fresh_analysis")
    op.execute("DROP TABLE public.mail_handoff")
