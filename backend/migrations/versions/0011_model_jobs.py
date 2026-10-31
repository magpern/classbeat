"""Model jobs record their route; results get a monotonic order (ADR 0012 section 5, M3c G2)

Revision ID: 0011_model_jobs
Revises: 0010_m5_hardening
Create Date: 2026-10-31

M3c G2 (#215).

- ``agent_runs`` gains ``route_key`` and ``eval_result_id``: the model route a stage run's
  provider, model and effort came from, both NULL for the bootstrap choice and ``fake``
  (``agent_runs_route``: both or neither). ``eval_result_id`` references the result the route
  stood on (``RESTRICT``; results are never deleted). Existing rows keep NULL: they all ran on
  the bootstrap choice or ``fake``.
- ``model_eval_results`` gains ``seq``, an identity column, so "the newest result" is
  deterministic also for results recorded in one transaction (same ``now()``): newest is
  ``recorded_at DESC, seq DESC`` (#215, item 3 of the #267 review). ``GENERATED ALWAYS``: an
  ``INSERT`` cannot choose it, and the app role has no ``UPDATE`` on the table or the sequence.
  The ordering index is replaced to match.

The queue functions read neither table, so their version and digests stay.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_model_jobs"
down_revision: str | Sequence[str] | None = "0010_m5_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = (
    "ALTER TABLE public.agent_runs "
    "ADD COLUMN route_key text CONSTRAINT agent_runs_route_key "
    "CHECK (route_key ~ '^[a-z0-9_]{1,40}$'), "
    "ADD COLUMN eval_result_id uuid CONSTRAINT agent_runs_eval_result_fkey "
    "REFERENCES public.model_eval_results (id) ON UPDATE RESTRICT ON DELETE RESTRICT, "
    "ADD CONSTRAINT agent_runs_route CHECK ((route_key IS NULL) = (eval_result_id IS NULL))",
    "CREATE INDEX agent_runs_eval_result ON public.agent_runs (eval_result_id) "
    "WHERE eval_result_id IS NOT NULL",
    "ALTER TABLE public.model_eval_results ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY",
    "ALTER TABLE public.model_eval_results ADD CONSTRAINT model_eval_results_seq_key UNIQUE (seq)",
    "DROP INDEX public.model_eval_results_newest",
    "CREATE INDEX model_eval_results_newest ON public.model_eval_results "
    "(provider, model, effort, stage_group, recorded_at DESC, seq DESC)",
)


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX public.model_eval_results_newest")
    op.execute(
        "CREATE INDEX model_eval_results_newest ON public.model_eval_results "
        "(provider, model, effort, stage_group, recorded_at DESC, id DESC)"
    )
    op.execute("ALTER TABLE public.model_eval_results DROP COLUMN seq")
    op.execute("DROP INDEX public.agent_runs_eval_result")
    op.execute(
        "ALTER TABLE public.agent_runs DROP CONSTRAINT agent_runs_route, "
        "DROP COLUMN eval_result_id, DROP COLUMN route_key"
    )
