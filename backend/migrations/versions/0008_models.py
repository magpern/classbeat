"""Eval results and model routes: the database half of the enable gate (ADR 0012 section 5)

Revision ID: 0008_models
Revises: 0007_tiers
Create Date: 2026-10-31

M3c G1 (#211). ``DDL`` is ADR 0012 section 5's SQL block **byte for byte**
(``tests/test_models_gate.py`` compares them), the two ``REVOKE``s included; the migration adds
only the indexes and comments below. It was checked as ``classbeat_app`` in a throwaway
``postgres:17.11`` against the 73 must-fail and 8 must-succeed cases of PR #229 before it was
written, and the same cases are tests now.

- Both tables are system tables (``SYSTEM_TABLES``): global, no ``owner_id``, no row security.
- Every ``CHECK`` is written ``CHECK (coalesce(<expr>, false))``, so none passes by being NULL;
  every column is ``NOT NULL``; the routes' foreign key is ``MATCH FULL``, ``RESTRICT`` both ways
  and not deferrable, so the database refuses a route whose result is not a pass for exactly that
  provider, model, effort and stage group.
- Results are append-only for the app role (``SELECT, INSERT``); routes get ``SELECT, INSERT,
  UPDATE, DELETE`` and no ``TRUNCATE``. ``docker/postgres/init/10-roles.sql`` revokes the same on
  results after its re-grant, so a re-run does not give them back.
- The only application writer of results is ``classbeat.models.store.record_result``
  (``tests/test_models_guard.py``).

The queue functions (``docker/postgres/queue/20-queue.sql``) read neither table, so their
version and digests stay.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_models"
down_revision: str | Sequence[str] | None = "0007_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ADR 0012 section 5, exactly (the test compares this string with the ADR's block).
DDL = """\
-- Rule: every CHECK is NULL-proof, written CHECK (coalesce(<expr>, false)), so none can pass by
-- evaluating to NULL. Every column the foreign key uses is NOT NULL and the key is MATCH FULL.
CREATE TABLE model_eval_results (
    id                 uuid        NOT NULL PRIMARY KEY,
    run_id             uuid        NOT NULL,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    suite_version      integer     NOT NULL CHECK (coalesce(suite_version >= 1, false)),
    manifest_digest    text        NOT NULL CHECK (coalesce(manifest_digest ~ '^[0-9a-f]{64}$', false)),
    provider           text        NOT NULL
        CHECK (coalesce(provider IN ('anthropic', 'openai', 'deepseek'), false)),
    model              text        NOT NULL CHECK (coalesce(model ~ '^[A-Za-z0-9._:-]{1,100}$', false)),
    effort             text        NOT NULL
        CHECK (coalesce(effort IN ('low', 'medium', 'high', 'xhigh', 'max'), false)),
    stage_group        text        NOT NULL CHECK (coalesce(stage_group IN ('design', 'songs'), false)),
    prompt_versions    jsonb       NOT NULL
        CHECK (coalesce(jsonb_typeof(prompt_versions) = 'object', false)),
    classbeat_version  text        NOT NULL
        CHECK (coalesce(char_length(classbeat_version) BETWEEN 1 AND 64, false)),
    results_sha256     text        NOT NULL CHECK (coalesce(results_sha256 ~ '^[0-9a-f]{64}$', false)),
    verdict            jsonb       NOT NULL,
    passed             boolean     NOT NULL,
    total_cost_micros  bigint      NOT NULL CHECK (coalesce(total_cost_micros >= 0, false)),
    authorization_note text        NOT NULL
        CHECK (coalesce(char_length(authorization_note) BETWEEN 1 AND 200, false)),
    CONSTRAINT model_eval_results_passed_matches_verdict CHECK (coalesce(
        jsonb_typeof(verdict) = 'object'
        AND verdict ? 'passed'
        AND jsonb_typeof(verdict -> 'passed') = 'boolean'
        AND passed = (verdict ->> 'passed')::boolean,
        false)),
    UNIQUE (run_id, stage_group),
    CONSTRAINT model_eval_results_fk_target UNIQUE (id, provider, model, effort, stage_group, passed)
);
-- Append-only for the app (the default privileges grant it SELECT, INSERT, UPDATE, DELETE).
REVOKE UPDATE, DELETE, TRUNCATE ON model_eval_results FROM classbeat_app;

CREATE TABLE model_routes (
    route_key      text        NOT NULL CHECK (coalesce(route_key ~ '^[a-z0-9_]{1,40}$', false)),
    stage_group    text        NOT NULL CHECK (coalesce(stage_group IN ('design', 'songs'), false)),
    provider       text        NOT NULL
        CHECK (coalesce(provider IN ('anthropic', 'openai', 'deepseek'), false)),
    model          text        NOT NULL,
    effort         text        NOT NULL,
    eval_result_id uuid        NOT NULL,
    gate_passed    boolean     NOT NULL DEFAULT true CHECK (coalesce(gate_passed, false)),
    set_at         timestamptz NOT NULL DEFAULT now(),
    set_by         text        NOT NULL CHECK (coalesce(char_length(set_by) BETWEEN 1 AND 200, false)),
    PRIMARY KEY (route_key, stage_group),
    CONSTRAINT model_routes_passing_result
        FOREIGN KEY (eval_result_id, provider, model, effort, stage_group, gate_passed)
        REFERENCES model_eval_results (id, provider, model, effort, stage_group, passed)
        MATCH FULL ON UPDATE RESTRICT ON DELETE RESTRICT
);
REVOKE TRUNCATE ON model_routes FROM classbeat_app;
"""

# What the migration adds besides the DDL (ADR 0012 section 5 allows indexes and comments).
EXTRAS = (
    # ``set_route``'s "newest result for this provider, model, effort and group" and the
    # staleness check's "a newer failing result".
    "CREATE INDEX model_eval_results_newest ON public.model_eval_results "
    "(provider, model, effort, stage_group, recorded_at DESC, id DESC)",
    # The foreign key's RESTRICT checks look results up from routes the other way round.
    "CREATE INDEX model_routes_eval_result ON public.model_routes (eval_result_id)",
    "COMMENT ON TABLE public.model_eval_results IS 'Recorded eval suite runs per stage group "
    "(ADR 0012 section 5); append-only for the app, written only by classbeat.models.store."
    "record_result'",
    "COMMENT ON TABLE public.model_routes IS 'Which provider, model and effort a route_key uses "
    "per stage group (ADR 0012 section 5); only at a recorded pass (model_routes_passing_result)'",
)


def upgrade() -> None:
    op.execute(DDL)
    for statement in EXTRAS:
        op.execute(statement)


def downgrade() -> None:
    # The matching grants of the upgrade's REVOKEs, then the tables (routes first: the FK).
    op.execute("GRANT TRUNCATE ON public.model_routes TO classbeat_app")
    op.execute("GRANT UPDATE, DELETE, TRUNCATE ON public.model_eval_results TO classbeat_app")
    op.execute("DROP TABLE public.model_routes")
    op.execute("DROP TABLE public.model_eval_results")
