"""The composer's pending chat messages (ADR 0038 section 8; #1186)

Revision ID: 0060_class_pending_messages
Revises: 0059_composer_flag
Create Date: 2026-11-18

- ``class_pending_messages``: a chat message the user sent while one of the class's jobs was
  queued or running, stored as the next message and submitted when the class's state allows
  (``classbeat.classes.pending``). ``message_id`` is the browser's own id, once per class, so a
  repeated send or submit is the same row. ``status`` is ``pending``, ``submitted`` (``job_id``
  names the job it became) or ``failed`` (``reason`` is the closed code that refused it, and only
  then). At most one message of a class is pending (a partial unique index). ``class_messages``
  cannot hold this: it is immutable (migration 0005) and a message's status changes.
  Owner RLS forced; goes with its class and the account; ``job_id`` is cleared with its job.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0060_class_pending_messages"
down_revision: str | Sequence[str] | None = "0059_composer_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PENDING = """
    CREATE TABLE public.class_pending_messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        class_id uuid NOT NULL,
        message_id uuid NOT NULL,
        body text NOT NULL
            CONSTRAINT class_pending_messages_body CHECK (char_length(body) BETWEEN 1 AND 2000),
        status text NOT NULL CONSTRAINT class_pending_messages_status
            CHECK (status IN ('pending', 'submitted', 'failed')),
        reason text CONSTRAINT class_pending_messages_reason CHECK (reason ~ '^[a-z_]{1,40}$'),
        job_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT class_pending_messages_failed CHECK ((status = 'failed') = (reason IS NOT NULL)),
        CONSTRAINT class_pending_messages_job CHECK (status = 'submitted' OR job_id IS NULL),
        CONSTRAINT class_pending_messages_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT class_pending_messages_owner_id_class_id_message_id_key
            UNIQUE (owner_id, class_id, message_id),
        CONSTRAINT class_pending_messages_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT class_pending_messages_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE SET NULL (job_id)
    )"""
ONE_PENDING = (
    "CREATE UNIQUE INDEX class_pending_messages_one_pending "
    "ON public.class_pending_messages (owner_id, class_id) WHERE status = 'pending'"
)
BY_JOB = "CREATE INDEX class_pending_messages_owner_job ON public.class_pending_messages (owner_id, job_id)"


def upgrade() -> None:
    op.execute(PENDING)
    enable_owner_rls("class_pending_messages")
    op.execute(ONE_PENDING)
    op.execute(BY_JOB)


def downgrade() -> None:
    op.execute("DROP TABLE public.class_pending_messages")
