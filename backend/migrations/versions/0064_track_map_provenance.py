"""Studio track maps analysed by spotdown, with their provenance, and spotdown's per-job budget
(ADR 0040 sections 6 to 8; SD-P5, #1302)

Revision ID: 0064_track_map_provenance
Revises: 0063_spotdown_shadow
Create Date: 2026-11-20

- ``swedebeat_track_maps`` gains ``analyser`` (``local``: ``mowl.music.analyze_track`` in the
  sandboxed child, every row so far, the default; or ``spotdown``: spotdown's ``analysis-v1`` of
  the uploaded bytes, mapped by ``spotdown_map_policy``) and ``provenance`` (for a ``spotdown`` row
  and only for one: ``provenance_model``'s record, 64 KiB at most; no title, reference, file name,
  URL, scope or client ref). The key becomes ``(owner_id, reference, analyser_version,
  analyser)``: a track's ``local`` and ``spotdown`` maps are two rows, and a job storing one never
  replaces the other (a class reads the analyser it was designed on).
- ``spotdown_studio_budgets``: one row per design job that used spotdown for its studio tracks
  (``classbeat.design.track_spotdown_budget``): when it first did (the start of the job's wait
  budget, over all its runs) and whether spotdown was found unreachable (then the whole job is
  analysed locally). No content. Owner RLS; gone with the job (and so the class) and the account
  by cascade.

The downgrade drops the budget table, the ``spotdown`` rows (a cache: they are analysed again),
both columns, and restores the old key.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0064_track_map_provenance"
down_revision: str | Sequence[str] | None = "0063_spotdown_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEY = "swedebeat_track_maps_owner_id_reference_analyser_version_key"
NEW_KEY = "swedebeat_track_maps_owner_reference_version_analyser_key"

TRACK_MAPS = (
    "ALTER TABLE public.swedebeat_track_maps "
    "ADD COLUMN analyser text NOT NULL DEFAULT 'local' "
    "CONSTRAINT swedebeat_track_maps_analyser CHECK (analyser IN ('local', 'spotdown')), "
    "ADD COLUMN provenance jsonb "
    "CONSTRAINT swedebeat_track_maps_provenance CHECK ("
    "jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 65536), "
    "ADD CONSTRAINT swedebeat_track_maps_provenance_spotdown "
    "CHECK ((analyser = 'spotdown') = (provenance IS NOT NULL)), "
    f"DROP CONSTRAINT {OLD_KEY}, "
    f"ADD CONSTRAINT {NEW_KEY} UNIQUE (owner_id, reference, analyser_version, analyser)"
)
BUDGETS = """
    CREATE TABLE public.spotdown_studio_budgets (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        job_id uuid NOT NULL,
        started_at timestamptz NOT NULL DEFAULT now(),
        outage boolean NOT NULL DEFAULT false,
        CONSTRAINT spotdown_studio_budgets_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT spotdown_studio_budgets_owner_job_key UNIQUE (owner_id, job_id),
        CONSTRAINT spotdown_studio_budgets_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE CASCADE
    )"""


def upgrade() -> None:
    op.execute(TRACK_MAPS)
    op.execute(BUDGETS)
    enable_owner_rls("spotdown_studio_budgets")


def downgrade() -> None:
    op.execute("DROP TABLE public.spotdown_studio_budgets")  # nothing refers to it
    # The owner role is subject to the forced policy: lift it for the repair, as 0053 does.
    op.execute("ALTER TABLE public.swedebeat_track_maps NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM public.swedebeat_track_maps WHERE analyser = 'spotdown'")
    op.execute("ALTER TABLE public.swedebeat_track_maps FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE public.swedebeat_track_maps "
        f"DROP CONSTRAINT {NEW_KEY}, "
        "DROP CONSTRAINT swedebeat_track_maps_provenance_spotdown, "
        "DROP COLUMN provenance, DROP COLUMN analyser, "
        f"ADD CONSTRAINT {OLD_KEY} UNIQUE (owner_id, reference, analyser_version)"
    )
