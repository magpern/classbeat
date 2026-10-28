"""jobs, events, external operations and agent runs (ADR 0005 section 1, ADR 0006 section 6)

Revision ID: 0004_jobs
Revises: 0003_connections
Create Date: 2026-10-28

Every table here is owned (ADR 0002 section 2): ``owner_id`` referencing ``users``, ``UNIQUE
(owner_id, id)``, owner-inclusive foreign keys, every other unique constraint or index including
``owner_id``, and ``enable_owner_rls``. ``class_revisions`` also gets the restrictive
``revisions_immutable`` policy, so no role (the owner included, RLS is forced) can update a
revision; referential actions run without row security, so deleting a class still cascades and
deleting a job still sets ``job_id`` to NULL. ``job_events`` is append-only the same way
(restrictive ``events_no_update`` and ``events_no_delete``); deleting a job or an account still
removes its events through the cascade.

The queue's ``SECURITY DEFINER`` functions are not created here: the superuser script
``docker/postgres/queue/20-queue.sql`` owns them (ADR 0005 section 3). A later migration that
changes a column those functions read bumps the script's version in the same PR.

The default privileges of ``classbeat_owner`` give ``classbeat_app`` DML on every new table. This
migration moves no data, so it needs no RLS bypass.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0004_jobs"
down_revision: str | Sequence[str] | None = "0003_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "classes",
    "class_revisions",
    "jobs",
    "job_events",
    "external_operations",
    "mowl_playlist_links",
    "agent_runs",
)

STATES = (
    "'queued', 'running', 'waiting_for_approval', 'completed', 'failed', 'cancelled', 'expired'"
)
TERMINAL = "'completed', 'failed', 'cancelled', 'expired'"
OPEN = "'queued', 'running', 'waiting_for_approval'"
# ADR 0005 section 4.
CATEGORIES = (
    "'upstream_unavailable', 'rate_limited', 'model_unavailable', 'lease_lost', "
    "'operation_busy', 'not_connected', 'needs_reconnect', 'key_unavailable', 'decrypt_failed', "
    "'invalid_input', 'account_disabled', 'model_refused', 'model_config', 'invalid_output', "
    "'limit_exceeded', 'uncertain_write', 'outbound_blocked', 'internal'"
)
# ADR 0005 section 6.
EVENT_KINDS = (
    "'queued', 'claimed', 'stage_started', 'tool_called', 'revision_created', "
    "'waiting_for_approval', 'approved', 'retry_scheduled', 'cancel_requested', 'cancelled', "
    "'completed', 'failed', 'expired', 'requeued', 'external_operation'"
)
SHA256 = "octet_length({}) = 32"
NAME = "'^[a-z_]{1,40}$'"


def _owner_columns() -> str:
    return """
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,"""


CLASSES = f"""
    CREATE TABLE public.classes ({_owner_columns()}
        title text NOT NULL CONSTRAINT classes_title CHECK (char_length(title) BETWEEN 1 AND 100),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT classes_owner_id_id_key UNIQUE (owner_id, id)
    )"""

# job_id's foreign key is added after ``jobs`` exists (the two tables refer to each other).
CLASS_REVISIONS = f"""
    CREATE TABLE public.class_revisions ({_owner_columns()}
        class_id uuid NOT NULL,
        number integer NOT NULL CONSTRAINT class_revisions_number CHECK (number >= 1),
        kind text NOT NULL CONSTRAINT class_revisions_kind CHECK (kind IN ('draft', 'song_list')),
        content jsonb NOT NULL
            CONSTRAINT class_revisions_content CHECK (octet_length(content::text) <= 65536),
        content_hash bytea NOT NULL
            CONSTRAINT class_revisions_content_hash CHECK ({SHA256.format("content_hash")}),
        music jsonb NOT NULL,
        provenance jsonb NOT NULL,
        job_id uuid,
        parent_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT class_revisions_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT class_revisions_owner_id_class_id_number_key UNIQUE (owner_id, class_id, number),
        CONSTRAINT class_revisions_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT class_revisions_parent_fkey FOREIGN KEY (owner_id, parent_id)
            REFERENCES public.class_revisions (owner_id, id) ON DELETE SET NULL (parent_id)
    )"""

REVISIONS_IMMUTABLE = """
    CREATE POLICY revisions_immutable ON public.class_revisions
        AS RESTRICTIVE FOR UPDATE TO PUBLIC USING (false)"""

JOBS = f"""
    CREATE TABLE public.jobs ({_owner_columns()}
        kind text NOT NULL CONSTRAINT jobs_kind CHECK (kind ~ {NAME}),
        state text NOT NULL CONSTRAINT jobs_state CHECK (state IN ({STATES})),
        stage text,
        class_id uuid NOT NULL,
        input jsonb NOT NULL CONSTRAINT jobs_input CHECK (octet_length(input::text) <= 16384),
        idempotency_key text NOT NULL
            CONSTRAINT jobs_idempotency_key CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{{16,64}}$'),
        request_hash bytea NOT NULL
            CONSTRAINT jobs_request_hash CHECK ({SHA256.format("request_hash")}),
        attempt integer NOT NULL DEFAULT 0,
        max_attempts integer NOT NULL DEFAULT 3
            CONSTRAINT jobs_max_attempts CHECK (max_attempts BETWEEN 1 AND 10),
        available_at timestamptz NOT NULL DEFAULT now(),
        lease_version bigint NOT NULL DEFAULT 0
            CONSTRAINT jobs_lease_version CHECK (lease_version >= 0),
        leased_by text CONSTRAINT jobs_leased_by CHECK (char_length(leased_by) <= 100),
        lease_expires_at timestamptz,
        heartbeat_at timestamptz,
        cancel_requested_at timestamptz,
        approval_revision_id uuid,
        approval_hash bytea CONSTRAINT jobs_approval_hash CHECK ({SHA256.format("approval_hash")}),
        expires_at timestamptz,
        approved_at timestamptz,
        error_category text CONSTRAINT jobs_error_category CHECK (error_category IN ({CATEGORIES})),
        credit_state text NOT NULL DEFAULT 'none'
            CONSTRAINT jobs_credit_state
            CHECK (credit_state IN ('none', 'reserved', 'committed', 'released')),
        next_seq bigint NOT NULL DEFAULT 0 CONSTRAINT jobs_next_seq CHECK (next_seq >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        started_at timestamptz,
        finished_at timestamptz,
        CONSTRAINT jobs_attempt CHECK (attempt >= 0 AND attempt <= max_attempts),
        CONSTRAINT jobs_lease CHECK (
            (state = 'running') = (leased_by IS NOT NULL)
            AND (state = 'running') = (lease_expires_at IS NOT NULL)),
        CONSTRAINT jobs_approval CHECK (
            state <> 'waiting_for_approval' OR (approval_hash IS NOT NULL AND expires_at IS NOT NULL)),
        CONSTRAINT jobs_failed CHECK ((state = 'failed') = (error_category IS NOT NULL)),
        CONSTRAINT jobs_finished CHECK ((state IN ({TERMINAL})) = (finished_at IS NOT NULL)),
        CONSTRAINT jobs_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT jobs_owner_id_idempotency_key_key UNIQUE (owner_id, idempotency_key),
        CONSTRAINT jobs_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT jobs_approval_revision_fkey FOREIGN KEY (owner_id, approval_revision_id)
            REFERENCES public.class_revisions (owner_id, id) ON DELETE SET NULL (approval_revision_id)
    )"""

REVISION_JOB_FKEY = """
    ALTER TABLE public.class_revisions ADD CONSTRAINT class_revisions_job_fkey
        FOREIGN KEY (owner_id, job_id) REFERENCES public.jobs (owner_id, id)
        ON DELETE SET NULL (job_id)"""

JOB_INDEXES = (
    f"CREATE UNIQUE INDEX jobs_one_open_per_class ON public.jobs (owner_id, class_id) "
    f"WHERE state IN ({OPEN})",
    "CREATE INDEX jobs_queued ON public.jobs (available_at) WHERE state = 'queued'",
    "CREATE INDEX jobs_running ON public.jobs (lease_expires_at) WHERE state = 'running'",
    "CREATE INDEX jobs_waiting ON public.jobs (expires_at) WHERE state = 'waiting_for_approval'",
    "CREATE INDEX jobs_owner_created ON public.jobs (owner_id, created_at)",
    "CREATE INDEX jobs_owner_class ON public.jobs (owner_id, class_id)",
    "CREATE INDEX class_revisions_owner_job ON public.class_revisions (owner_id, job_id)",
)

EVENTS_APPEND_ONLY = (
    "CREATE POLICY events_no_update ON public.job_events AS RESTRICTIVE FOR UPDATE TO PUBLIC "
    "USING (false)",
    "CREATE POLICY events_no_delete ON public.job_events AS RESTRICTIVE FOR DELETE TO PUBLIC "
    "USING (false)",
)

JOB_EVENTS = f"""
    CREATE TABLE public.job_events ({_owner_columns()}
        job_id uuid NOT NULL,
        seq bigint NOT NULL CONSTRAINT job_events_seq CHECK (seq >= 1),
        kind text NOT NULL CONSTRAINT job_events_kind CHECK (kind IN ({EVENT_KINDS})),
        payload jsonb NOT NULL DEFAULT '{{}}'::jsonb
            CONSTRAINT job_events_payload CHECK (octet_length(payload::text) <= 2048),
        lease_version bigint NOT NULL CONSTRAINT job_events_lease_version CHECK (lease_version >= 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT job_events_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT job_events_owner_id_job_id_seq_key UNIQUE (owner_id, job_id, seq),
        CONSTRAINT job_events_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE CASCADE
    )"""

EXTERNAL_OPERATIONS = f"""
    CREATE TABLE public.external_operations ({_owner_columns()}
        job_id uuid,
        kind text NOT NULL
            CONSTRAINT external_operations_kind CHECK (kind IN ('mowl_attach_spotify_playlist')),
        idempotency_key text NOT NULL
            CONSTRAINT external_operations_idempotency_key
            CHECK (char_length(idempotency_key) BETWEEN 1 AND 120),
        request_hash bytea NOT NULL
            CONSTRAINT external_operations_request_hash CHECK ({SHA256.format("request_hash")}),
        state text NOT NULL CONSTRAINT external_operations_state
            CHECK (state IN ('pending', 'succeeded', 'failed', 'uncertain')),
        external_id text
            CONSTRAINT external_operations_external_id CHECK (char_length(external_id) <= 200),
        result jsonb NOT NULL DEFAULT '{{}}'::jsonb
            CONSTRAINT external_operations_result CHECK (octet_length(result::text) <= 4096),
        error_category text CONSTRAINT external_operations_error_category
            CHECK (error_category IN ({CATEGORIES})),
        lease_version bigint NOT NULL
            CONSTRAINT external_operations_lease_version CHECK (lease_version >= 0),
        resolved_by text CONSTRAINT external_operations_resolved_by
            CHECK (resolved_by IN ('worker', 'user', 'admin')),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        resolved_at timestamptz,
        CONSTRAINT external_operations_succeeded
            CHECK (state <> 'succeeded' OR external_id IS NOT NULL),
        CONSTRAINT external_operations_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT external_operations_owner_id_kind_idempotency_key_key
            UNIQUE (owner_id, kind, idempotency_key),
        CONSTRAINT external_operations_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE SET NULL (job_id)
    )"""

MOWL_PLAYLIST_LINKS = f"""
    CREATE TABLE public.mowl_playlist_links ({_owner_columns()}
        source text NOT NULL CONSTRAINT mowl_playlist_links_source CHECK (source IN ('spotify')),
        source_id text NOT NULL
            CONSTRAINT mowl_playlist_links_source_id CHECK (source_id ~ '^[0-9A-Za-z]{{22}}$'),
        playlist_id bigint NOT NULL
            CONSTRAINT mowl_playlist_links_playlist_id CHECK (playlist_id > 0),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT mowl_playlist_links_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT mowl_playlist_links_owner_id_source_source_id_key
            UNIQUE (owner_id, source, source_id)
    )"""

# ADR 0006 section 6. ``outcome`` stays NULL while the run is in progress (its counters are
# written after every turn, so a crashed run still shows its cost) and is set with finished_at.
OUTCOMES = f"'submitted', 'cancelled', {CATEGORIES}"
COUNTERS = (
    "turns",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_micros",
)
AGENT_RUNS = f"""
    CREATE TABLE public.agent_runs ({_owner_columns()}
        job_id uuid NOT NULL,
        stage text NOT NULL CONSTRAINT agent_runs_stage CHECK (stage ~ {NAME}),
        attempt integer NOT NULL CONSTRAINT agent_runs_attempt CHECK (attempt >= 1),
        provider text NOT NULL,
        model text NOT NULL,
        model_answered text NOT NULL DEFAULT '',
        effort text NOT NULL,
        prompt_version text NOT NULL,
        {", ".join(f"{c} bigint NOT NULL DEFAULT 0 CHECK ({c} >= 0)" for c in COUNTERS)},
        outcome text CONSTRAINT agent_runs_outcome CHECK (outcome IN ({OUTCOMES})),
        started_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        CONSTRAINT agent_runs_finished CHECK ((outcome IS NULL) = (finished_at IS NULL)),
        CONSTRAINT agent_runs_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT agent_runs_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE CASCADE
    )"""

OTHER_INDEXES = (
    "CREATE INDEX external_operations_owner_job ON public.external_operations (owner_id, job_id)",
    "CREATE INDEX agent_runs_owner_job ON public.agent_runs (owner_id, job_id)",
)


def upgrade() -> None:
    op.execute(CLASSES)
    enable_owner_rls("classes")
    op.execute(CLASS_REVISIONS)
    enable_owner_rls("class_revisions")
    op.execute(REVISIONS_IMMUTABLE)
    op.execute(JOBS)
    enable_owner_rls("jobs")
    op.execute(REVISION_JOB_FKEY)
    for statement in JOB_INDEXES:
        op.execute(statement)
    for table, ddl in (
        ("job_events", JOB_EVENTS),
        ("external_operations", EXTERNAL_OPERATIONS),
        ("mowl_playlist_links", MOWL_PLAYLIST_LINKS),
        ("agent_runs", AGENT_RUNS),
    ):
        op.execute(ddl)
        enable_owner_rls(table)
    for statement in (*EVENTS_APPEND_ONLY, *OTHER_INDEXES):
        op.execute(statement)


def downgrade() -> None:
    # The queue functions stay (they are the superuser script's); they name these tables only at
    # run time, and the script grants classbeat_queue its privileges again after the next upgrade.
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
