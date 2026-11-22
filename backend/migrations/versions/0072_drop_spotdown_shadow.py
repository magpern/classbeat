"""drop spotdown_shadow_tracks: shadow mode is gone (ADR 0040 amendment; #1355)

Revision ID: 0072_drop_spotdown_shadow
Revises: 0071_mix_not_applied
Create Date: 2026-11-22

Shadow mode only compared spotdown with the local analysis, and spotdown is the only analysis path
now (ADR 0040 amendment "spotdown is the only analysis path", items 2 and 3): nothing writes
``spotdown_shadow_tracks`` any more, so it goes, with its owner row security and indexes.

The downgrade recreates the table as 0063 made it (empty, with its owner row security and
indexes); the rows are not restored. ``spotdown_scope_deletions`` (0063's other table) stays.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0072_drop_spotdown_shadow"
down_revision: str | Sequence[str] | None = "0071_mix_not_applied"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The outcome codes of the table as 0063 made it (kept here: the module that listed them is gone).
OUTCOMES = (
    "'pending', 'compared', 'track_failure', 'unreachable', 'timed_out', 'bad_document', "
    "'internal'"
)

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
INDEXES = (
    "CREATE INDEX spotdown_shadow_tracks_owner_job ON public.spotdown_shadow_tracks "
    "(owner_id, job_id)",
    "CREATE INDEX spotdown_shadow_tracks_owner_started ON public.spotdown_shadow_tracks "
    "(owner_id, started_at)",
)


def upgrade() -> None:
    op.execute("DROP TABLE public.spotdown_shadow_tracks")


def downgrade() -> None:
    op.execute(SHADOW_TRACKS)
    enable_owner_rls("spotdown_shadow_tracks")
    for statement in INDEXES:
        op.execute(statement)
