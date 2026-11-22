"""spotdown shadow mode for studio tracks (ADR 0040 sections 6, 7 and 9; SD-P4 part 3, #1301)

Revision ID: 0063_spotdown_shadow
Revises: 0062_revert_email_token
Create Date: 2026-11-20

- ``spotdown_shadow_tracks``: one row per studio track a design job relayed to spotdown in shadow
  mode (``classbeat.design.track_shadow``): the outcome (a code), an optional detail code, the
  comparison metrics (numbers, booleans and codes only; 16 KiB at most) and the provenance record
  of the analysis (ids, versions, the audio identity, the timing class and its evidence; 64 KiB at
  most), when the relay started and when it ended. No title, reference, file name, URL, scope or
  client ref, no audio. Owner RLS; gone with the job (and so the class) and the account by
  cascade, with the SwedeBeat connection (``connections.forget``) and after 90 days (the
  application's).
- ``spotdown_scope_deletions``: a system table, the outbox of ``DELETE /v2/scopes/{scope}`` for a
  deleted account (``classbeat.spotdown.scope_outbox``). Only the opaque scope (an HMAC of the
  owner id, never the id) and its delivery state: the web app writes it before the ``users`` row
  goes and the worker, the only process that reaches spotdown, delivers it. The app role has its
  DML (the default privileges); ``classbeat_maint`` has nothing on it.

The downgrade drops both tables (nothing refers to either).
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0063_spotdown_shadow"
down_revision: str | Sequence[str] | None = "0062_revert_email_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOMES = (
    "'pending', 'compared', 'track_failure', 'unreachable', 'timed_out', 'bad_document', "
    "'internal'"
)  # the outcomes of the shadow comparison (the code that wrote them is gone, 0073)

SHADOW_TRACKS = f"""
    CREATE TABLE public.spotdown_shadow_tracks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        job_id uuid NOT NULL,
        outcome text NOT NULL
            CONSTRAINT spotdown_shadow_tracks_outcome CHECK (outcome IN ({OUTCOMES})),
        detail text
            CONSTRAINT spotdown_shadow_tracks_detail CHECK (detail ~ '^[a-z_]{{1,40}}$'),
        metrics jsonb NOT NULL DEFAULT '{{}}'::jsonb
            CONSTRAINT spotdown_shadow_tracks_metrics CHECK (
                jsonb_typeof(metrics) = 'object' AND octet_length(metrics::text) <= 16384
            ),
        provenance jsonb
            CONSTRAINT spotdown_shadow_tracks_provenance CHECK (
                jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 65536
            ),
        started_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        CONSTRAINT spotdown_shadow_tracks_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT spotdown_shadow_tracks_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE CASCADE
    )"""
SCOPE_DELETIONS = """
    CREATE TABLE public.spotdown_scope_deletions (
        scope text PRIMARY KEY
            CONSTRAINT spotdown_scope_deletions_scope CHECK (scope ~ '^[A-Za-z0-9_-]{1,128}$'),
        attempts integer NOT NULL DEFAULT 0
            CONSTRAINT spotdown_scope_deletions_attempts CHECK (attempts >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        next_attempt_at timestamptz NOT NULL DEFAULT now()
    )"""
INDEXES = (
    "CREATE INDEX spotdown_shadow_tracks_owner_job ON public.spotdown_shadow_tracks "
    "(owner_id, job_id)",
    "CREATE INDEX spotdown_shadow_tracks_owner_started ON public.spotdown_shadow_tracks "
    "(owner_id, started_at)",
    "CREATE INDEX spotdown_scope_deletions_due ON public.spotdown_scope_deletions "
    "(next_attempt_at)",
)


def upgrade() -> None:
    op.execute(SHADOW_TRACKS)
    enable_owner_rls("spotdown_shadow_tracks")
    op.execute(SCOPE_DELETIONS)
    for statement in INDEXES:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE public.spotdown_scope_deletions")
    op.execute("DROP TABLE public.spotdown_shadow_tracks")
