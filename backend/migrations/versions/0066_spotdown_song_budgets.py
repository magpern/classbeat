"""spotdown's match budget of a Spotify song pick (ADR 0040 section 8; SD-P6 stage A, #1303)

Revision ID: 0066_spotdown_song_budgets
Revises: 0063_spotdown_shadow
Create Date: 2026-11-20

- ``spotdown_song_budgets``: one row per song job (``design_with_songs``, ``revise_songs``) whose
  owner the spotdown match is on for (``classbeat.songs.pick_match_budget``): the seconds the job
  has waited for spotdown so far, over all its runs (the "about 10 min" wait budget of section 8),
  and whether the class fell back to today's path (spotdown unreachable or a provider outage), so
  a later run of the job does not call spotdown again. No content: two numbers and a flag. Owner
  RLS; gone with the job (and so the class) and the account by cascade.

Numbered 0066 because #1320 (0064) and #1326 (0065) take the two before it. It revises 0063, the
head on ``main`` now; when those land first, this ``down_revision`` is relinked to their head in
the merge that brings them (``test_migrations`` pins the single head, so two heads fail there).

The downgrade drops the table (nothing refers to it).
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0066_spotdown_song_budgets"
down_revision: str | Sequence[str] | None = "0063_spotdown_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUDGETS = """
    CREATE TABLE public.spotdown_song_budgets (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
        job_id uuid NOT NULL,
        spent_seconds double precision NOT NULL DEFAULT 0
            CONSTRAINT spotdown_song_budgets_spent CHECK (
                spent_seconds >= 0 AND spent_seconds <= 86400
            ),
        fallback boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT spotdown_song_budgets_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT spotdown_song_budgets_owner_job_key UNIQUE (owner_id, job_id),
        CONSTRAINT spotdown_song_budgets_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE CASCADE
    )"""


def upgrade() -> None:
    op.execute(BUDGETS)
    enable_owner_rls("spotdown_song_budgets")


def downgrade() -> None:
    op.execute("DROP TABLE public.spotdown_song_budgets")
