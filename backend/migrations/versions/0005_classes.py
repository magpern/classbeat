"""classes, conversation, saved workouts and durable usage (ADR 0007 sections 1, 2, 6 to 8, 10, 11)

Revision ID: 0005_classes
Revises: 0004_jobs
Create Date: 2026-10-29

M3b's schema, issue C1 (#141). Every new table is owned (ADR 0002 section 2): ``owner_id``
referencing ``users``, ``UNIQUE (owner_id, id)``, owner-inclusive foreign keys, every other
unique constraint including ``owner_id``, ``enable_owner_rls``.

Where the ADR says "owner-inclusive foreign key" to a revision, the key here also includes
``class_id`` (``UNIQUE (owner_id, class_id, id)`` on ``class_revisions``): a pointer at a revision
can then only name a revision of the *same class*, not any revision of the same owner. That is
the stricter reading of the ADR and costs one index.

``agent_runs`` becomes the durable usage record (section 10). Its restrictive policies are created
**after** the backfill of ``job_kind``: the backfill updates finished rows, which the policy would
refuse. The owner role is held to row security (FORCE) and sets no ``app.user_id``, so the backfill
and the downgrade's data repairs run with FORCE switched off for the tables they read or change
(the table owner is then exempt) and switched on again before the migration ends; a migration is one transaction.

The queue functions (``docker/postgres/queue/20-queue.sql``) read ``jobs``, ``job_events`` and
``external_operations`` columns that do not change, so their version stays.
"""

from collections.abc import Sequence

from alembic import op

from classbeat.db.rls import enable_owner_rls

revision: str = "0005_classes"
down_revision: str | Sequence[str] | None = "0004_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CATEGORIES = (
    "'upstream_unavailable', 'rate_limited', 'model_unavailable', 'lease_lost', "
    "'operation_busy', 'not_connected', 'needs_reconnect', 'key_unavailable', 'decrypt_failed', "
    "'invalid_input', 'account_disabled', 'model_refused', 'model_config', 'invalid_output', "
    "'limit_exceeded', 'uncertain_write', 'outbound_blocked', 'internal'"
)
# ADR 0007 section 1 and 12: the same list in the three checks below.
NEW_ONLY = "'program_changed', 'program_missing', 'music_unlinked'"
CATEGORIES = f"{OLD_CATEGORIES}, {NEW_ONLY}"
SHA256 = "octet_length({}) = 32"
NAME = "'^[a-z_]{1,40}$'"
OLD_KINDS = "'mowl_attach_spotify_playlist'"
KINDS = f"{OLD_KINDS}, 'mowl_save_workout', 'mowl_update_workout'"


def _owner_columns() -> str:
    return """
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,"""


CLASS_MESSAGES = f"""
    CREATE TABLE public.class_messages ({_owner_columns()}
        class_id uuid NOT NULL,
        seq integer NOT NULL CONSTRAINT class_messages_seq CHECK (seq >= 1),
        role text NOT NULL CONSTRAINT class_messages_role CHECK (role IN ('user', 'assistant')),
        body text NOT NULL,
        job_id uuid,
        base_revision_id uuid,
        result_revision_id uuid,
        change jsonb CONSTRAINT class_messages_change CHECK (octet_length(change::text) <= 1024),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT class_messages_body CHECK (
            (role = 'user' AND char_length(body) BETWEEN 1 AND 2000)
            OR (role = 'assistant' AND char_length(body) BETWEEN 0 AND 300)),
        CONSTRAINT class_messages_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT class_messages_owner_id_class_id_seq_key UNIQUE (owner_id, class_id, seq),
        CONSTRAINT class_messages_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT class_messages_job_fkey FOREIGN KEY (owner_id, job_id)
            REFERENCES public.jobs (owner_id, id) ON DELETE SET NULL (job_id),
        CONSTRAINT class_messages_base_revision_fkey
            FOREIGN KEY (owner_id, class_id, base_revision_id)
            REFERENCES public.class_revisions (owner_id, class_id, id)
            ON DELETE SET NULL (base_revision_id),
        CONSTRAINT class_messages_result_revision_fkey
            FOREIGN KEY (owner_id, class_id, result_revision_id)
            REFERENCES public.class_revisions (owner_id, class_id, id)
            ON DELETE SET NULL (result_revision_id)
    )"""

# Immutable like job_events (ADR 0007 section 1): cascades and SET NULL still run (they skip row
# security), nothing else can change or remove a message.
CLASS_MESSAGES_IMMUTABLE = (
    "CREATE POLICY messages_no_update ON public.class_messages AS RESTRICTIVE FOR UPDATE "
    "TO PUBLIC USING (false)",
    "CREATE POLICY messages_no_delete ON public.class_messages AS RESTRICTIVE FOR DELETE "
    "TO PUBLIC USING (false)",
)

SAVED_WORKOUTS = f"""
    CREATE TABLE public.saved_workouts ({_owner_columns()}
        class_id uuid NOT NULL,
        program_id bigint NOT NULL CONSTRAINT saved_workouts_program_id CHECK (program_id > 0),
        playlist_id bigint CONSTRAINT saved_workouts_playlist_id CHECK (playlist_id > 0),
        revision_id uuid,
        revision_number integer NOT NULL
            CONSTRAINT saved_workouts_revision_number CHECK (revision_number >= 1),
        content_hash bytea NOT NULL
            CONSTRAINT saved_workouts_content_hash CHECK ({SHA256.format("content_hash")}),
        state_hash bytea
            CONSTRAINT saved_workouts_state_hash CHECK ({SHA256.format("state_hash")}),
        name text NOT NULL CONSTRAINT saved_workouts_name CHECK (char_length(name) BETWEEN 1 AND 100),
        operation_id uuid,
        detached_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT saved_workouts_owner_id_id_key UNIQUE (owner_id, id),
        CONSTRAINT saved_workouts_class_fkey FOREIGN KEY (owner_id, class_id)
            REFERENCES public.classes (owner_id, id) ON DELETE CASCADE,
        CONSTRAINT saved_workouts_revision_fkey FOREIGN KEY (owner_id, class_id, revision_id)
            REFERENCES public.class_revisions (owner_id, class_id, id)
            ON DELETE SET NULL (revision_id),
        CONSTRAINT saved_workouts_operation_fkey FOREIGN KEY (owner_id, operation_id)
            REFERENCES public.external_operations (owner_id, id) ON DELETE SET NULL (operation_id)
    )"""

INDEXES = (
    # The tree and the pointer: a revision is only ever named together with its class.
    "ALTER TABLE public.class_revisions ADD CONSTRAINT class_revisions_owner_id_class_id_id_key "
    "UNIQUE (owner_id, class_id, id)",
    "CREATE UNIQUE INDEX saved_workouts_live_program ON public.saved_workouts "
    "(owner_id, program_id) WHERE detached_at IS NULL",
    # Foreign-key columns whose parent deletion runs a SET NULL or a cascade.
    "CREATE INDEX class_messages_owner_job ON public.class_messages (owner_id, job_id)",
    "CREATE INDEX class_messages_owner_base ON public.class_messages (owner_id, base_revision_id)",
    "CREATE INDEX class_messages_owner_result "
    "ON public.class_messages (owner_id, result_revision_id)",
    "CREATE INDEX saved_workouts_owner_class ON public.saved_workouts (owner_id, class_id)",
    "CREATE INDEX saved_workouts_owner_revision "
    "ON public.saved_workouts (owner_id, revision_id)",
    "CREATE INDEX saved_workouts_owner_operation "
    "ON public.saved_workouts (owner_id, operation_id)",
    "CREATE INDEX external_operations_owner_class "
    "ON public.external_operations (owner_id, class_id)",
    "CREATE INDEX classes_owner_current ON public.classes (owner_id, current_revision_id)",
    "CREATE INDEX agent_runs_owner_started ON public.agent_runs (owner_id, started_at)",
)

CLASSES_ALTER = (
    "ALTER TABLE public.classes ADD COLUMN current_revision_id uuid",
    "ALTER TABLE public.classes ADD COLUMN brief jsonb "
    "CONSTRAINT classes_brief CHECK (octet_length(brief::text) <= 4096)",
    "ALTER TABLE public.classes ADD COLUMN music_source jsonb "
    "CONSTRAINT classes_music_source CHECK (octet_length(music_source::text) <= 1024)",
)
# Keyed on (owner, class, revision): the current revision is one of the class's own.
CLASSES_CURRENT_FKEY = """
    ALTER TABLE public.classes ADD CONSTRAINT classes_current_revision_fkey
        FOREIGN KEY (owner_id, id, current_revision_id)
        REFERENCES public.class_revisions (owner_id, class_id, id)
        ON DELETE SET NULL (current_revision_id)"""

EXTERNAL_OPERATIONS_ALTER = (
    "ALTER TABLE public.external_operations ADD COLUMN class_id uuid",
    "ALTER TABLE public.external_operations ADD COLUMN request_context jsonb NOT NULL "
    "DEFAULT '{}'::jsonb CONSTRAINT external_operations_request_context "
    "CHECK (octet_length(request_context::text) <= 2048)",
    "ALTER TABLE public.external_operations ADD COLUMN pending_at timestamptz",
    "ALTER TABLE public.external_operations ADD COLUMN call_ended_at timestamptz",
    "ALTER TABLE public.external_operations ADD CONSTRAINT external_operations_class_fkey "
    "FOREIGN KEY (owner_id, class_id) REFERENCES public.classes (owner_id, id) ON DELETE CASCADE",
    # A save or update operation always belongs to a class, so it goes with the class (section 11);
    # an attach never does (it is the user's, kept as ADR 0006 section 3 says), so a class delete
    # can't cascade it away (#141 review, X2).
    "ALTER TABLE public.external_operations ADD CONSTRAINT external_operations_class "
    "CHECK ((kind <> 'mowl_attach_spotify_playlist' OR class_id IS NULL) "
    "AND (kind NOT IN ('mowl_save_workout', 'mowl_update_workout') OR class_id IS NOT NULL))",
)


def _replace_check(table: str, constraint: str, column: str, values: str) -> list[str]:
    return [
        f"ALTER TABLE public.{table} DROP CONSTRAINT {constraint}",
        f"ALTER TABLE public.{table} ADD CONSTRAINT {constraint} "
        f"CHECK ({column} IN ({values}))",
    ]


def _force_rls(table: str, forced: bool) -> str:
    return f"ALTER TABLE public.{table} {'FORCE' if forced else 'NO FORCE'} ROW LEVEL SECURITY"


def upgrade() -> None:
    # classes: the pointer needs class_revisions' (owner, class, id) key first.
    op.execute(INDEXES[0])
    for statement in CLASSES_ALTER:
        op.execute(statement)
    op.execute(CLASSES_CURRENT_FKEY)

    op.execute(CLASS_MESSAGES)
    enable_owner_rls("class_messages")
    for statement in CLASS_MESSAGES_IMMUTABLE:
        op.execute(statement)

    # external_operations: columns, the two new kinds, the new categories.
    for statement in EXTERNAL_OPERATIONS_ALTER[:-1]:
        op.execute(statement)
    # Rows that exist were inserted pending (M3a); their pending_at is their creation time.
    op.execute(_force_rls("external_operations", False))
    op.execute("UPDATE public.external_operations SET pending_at = created_at")
    op.execute(_force_rls("external_operations", True))
    op.execute(EXTERNAL_OPERATIONS_ALTER[-1])
    for statement in (
        *_replace_check("external_operations", "external_operations_kind", "kind", KINDS),
        *_replace_check(
            "external_operations", "external_operations_error_category", "error_category",
            CATEGORIES,
        ),
        *_replace_check("jobs", "jobs_error_category", "error_category", CATEGORIES),
    ):  # fmt: skip
        op.execute(statement)

    op.execute(SAVED_WORKOUTS)
    enable_owner_rls("saved_workouts")

    # agent_runs: the durable usage record. Columns, the backfill, then the policies.
    op.execute("ALTER TABLE public.agent_runs ALTER COLUMN job_id DROP NOT NULL")
    op.execute("ALTER TABLE public.agent_runs DROP CONSTRAINT agent_runs_job_fkey")
    op.execute(
        "ALTER TABLE public.agent_runs ADD CONSTRAINT agent_runs_job_fkey "
        "FOREIGN KEY (owner_id, job_id) REFERENCES public.jobs (owner_id, id) "
        "ON DELETE SET NULL (job_id)"
    )
    # The defaults keep M3a's insert working until US1 (#145) writes the columns and drops them.
    op.execute(
        f"ALTER TABLE public.agent_runs ADD COLUMN job_kind text NOT NULL DEFAULT 'unknown' "
        f"CONSTRAINT agent_runs_job_kind CHECK (job_kind ~ {NAME})"
    )
    op.execute(
        "ALTER TABLE public.agent_runs ADD COLUMN price_version text NOT NULL DEFAULT 'unknown' "
        "CONSTRAINT agent_runs_price_version CHECK (char_length(price_version) BETWEEN 1 AND 40)"
    )
    # ``jobs`` is read for the backfill, so its row security is switched off as well.
    for table in ("agent_runs", "jobs"):
        op.execute(_force_rls(table, False))
    op.execute(
        "UPDATE public.agent_runs r SET job_kind = j.kind FROM public.jobs j "
        "WHERE j.owner_id = r.owner_id AND j.id = r.job_id"
    )
    for table in ("agent_runs", "jobs"):
        op.execute(_force_rls(table, True))
    for statement in _replace_check(
        "agent_runs", "agent_runs_outcome", "outcome", f"'submitted', 'cancelled', {CATEGORIES}"
    ):
        op.execute(statement)
    for statement in INDEXES[1:]:
        op.execute(statement)
    # Section 1: WITH CHECK (true) is required, or the USING expression would also be applied to
    # the new row and the fenced update that sets ``outcome`` would be refused.
    op.execute(
        "CREATE POLICY agent_runs_finished_immutable ON public.agent_runs "
        "AS RESTRICTIVE FOR UPDATE TO PUBLIC USING (outcome IS NULL) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY agent_runs_no_delete ON public.agent_runs "
        "AS RESTRICTIVE FOR DELETE TO PUBLIC USING (false)"
    )
    # Defence in depth next to the policies: privileges the app role has no use for. Referential
    # actions (a class's or an account's deletion) run as the table owner and need none of them.
    op.execute("REVOKE UPDATE, DELETE ON TABLE public.class_messages FROM classbeat_app")
    op.execute("REVOKE DELETE ON TABLE public.agent_runs FROM classbeat_app")


def downgrade() -> None:
    op.execute("DROP POLICY agent_runs_no_delete ON public.agent_runs")
    op.execute("DROP POLICY agent_runs_finished_immutable ON public.agent_runs")
    op.execute("GRANT UPDATE, DELETE ON TABLE public.class_messages TO classbeat_app")
    op.execute("GRANT DELETE ON TABLE public.agent_runs TO classbeat_app")
    op.execute("DROP TABLE public.saved_workouts")
    op.execute("DROP TABLE public.class_messages")

    # Data that the older schema can't hold goes: save and update operations, runs whose job is
    # gone, and the three new categories become ``internal``. The owner is exempt from row security
    # while it is not forced, so this reaches every user's rows.
    for table in ("external_operations", "agent_runs", "jobs"):
        op.execute(_force_rls(table, False))
    op.execute(
        f"DELETE FROM public.external_operations WHERE kind NOT IN ({OLD_KINDS})"
    )
    op.execute("DELETE FROM public.agent_runs WHERE job_id IS NULL")
    for table, column in (
        ("jobs", "error_category"),
        ("external_operations", "error_category"),
        ("agent_runs", "outcome"),
    ):
        op.execute(
            f"UPDATE public.{table} SET {column} = 'internal' WHERE {column} IN ({NEW_ONLY})"
        )
    for table in ("external_operations", "agent_runs", "jobs"):
        op.execute(_force_rls(table, True))
    for statement in (
        *_replace_check("jobs", "jobs_error_category", "error_category", OLD_CATEGORIES),
        *_replace_check(
            "external_operations", "external_operations_error_category", "error_category",
            OLD_CATEGORIES,
        ),
        *_replace_check("external_operations", "external_operations_kind", "kind", OLD_KINDS),
        *_replace_check(
            "agent_runs", "agent_runs_outcome", "outcome",
            f"'submitted', 'cancelled', {OLD_CATEGORIES}",
        ),
    ):  # fmt: skip
        op.execute(statement)

    for statement in (
        "DROP INDEX public.agent_runs_owner_started",
        "DROP INDEX public.external_operations_owner_class",
        "ALTER TABLE public.agent_runs DROP COLUMN job_kind",
        "ALTER TABLE public.agent_runs DROP COLUMN price_version",
        "ALTER TABLE public.agent_runs DROP CONSTRAINT agent_runs_job_fkey",
        "ALTER TABLE public.agent_runs ALTER COLUMN job_id SET NOT NULL",
        "ALTER TABLE public.agent_runs ADD CONSTRAINT agent_runs_job_fkey "
        "FOREIGN KEY (owner_id, job_id) REFERENCES public.jobs (owner_id, id) ON DELETE CASCADE",
        "ALTER TABLE public.external_operations DROP CONSTRAINT external_operations_class",
        "ALTER TABLE public.external_operations DROP COLUMN class_id",
        "ALTER TABLE public.external_operations DROP COLUMN request_context",
        "ALTER TABLE public.external_operations DROP COLUMN pending_at",
        "ALTER TABLE public.external_operations DROP COLUMN call_ended_at",
        "DROP INDEX public.classes_owner_current",
        "ALTER TABLE public.classes DROP COLUMN current_revision_id",
        "ALTER TABLE public.classes DROP COLUMN brief",
        "ALTER TABLE public.classes DROP COLUMN music_source",
        "ALTER TABLE public.class_revisions "
        "DROP CONSTRAINT class_revisions_owner_id_class_id_id_key",
    ):
        op.execute(statement)
