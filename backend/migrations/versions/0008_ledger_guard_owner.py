"""The ledger trigger's cascade branch only for the table owner (review of #233, issue AR1 #210)

Revision ID: 0008_ledger_guard_owner
Revises: 0007_tiers
Create Date: 2026-10-31

``credit_ledger_guard`` (0007) let **every** role set ``job_id`` to NULL when nothing else
changed. Only the foreign key's ``ON DELETE SET NULL (job_id)`` needs it, and that referential
action runs as the owner of ``credit_ledger`` (``classbeat_owner``). For the app role the policy
``credit_ledger_settle`` already refused it on a reserved row, but ``classbeat_maint`` (AR1,
BYPASSRLS) is not bound by policies: nulling the job of a live reservation would stop it counting
(ADR 0011 section 3, "held") and make the class free. The branch now requires ``current_user =
'classbeat_owner'``; the rest of the function is unchanged.

The function's digest is pinned in ``classbeat/db/queue_digests.py`` and in
``docker/postgres/queue/20-queue.sql`` (ADR 0005 section 3, amendment of 2026-10-30), both
updated with this migration. ``CREATE OR REPLACE`` keeps the owner, the ACL and the trigger.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_ledger_guard_owner"
down_revision: str | Sequence[str] | None = "0007_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BODY = """
    CREATE OR REPLACE FUNCTION public.credit_ledger_guard() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, pg_temp AS $$
    BEGIN
        -- Every role: the cascade of a deleted job (ON DELETE SET NULL (job_id)), which runs as
        -- the table owner.
        IF __CASCADE__OLD.job_id IS NOT NULL AND NEW.job_id IS NULL
                AND to_jsonb(NEW) - 'job_id' = to_jsonb(OLD) - 'job_id' THEN
            RETURN NEW;
        END IF;
        -- The app role's settle: reserved -> committed | released.
        IF current_user = 'classbeat_app' AND OLD.state = 'reserved'
                AND NEW.state IN ('committed', 'released')
                AND to_jsonb(NEW) - ARRAY['state', 'reason', 'settled_at']
                    = to_jsonb(OLD) - ARRAY['state', 'reason', 'settled_at'] THEN
            RETURN NEW;
        END IF;
        -- The admin refund: committed -> refunded, never a live reservation.
        IF current_user = 'classbeat_maint' AND OLD.state = 'committed'
                AND NEW.state = 'refunded'
                AND to_jsonb(NEW) - ARRAY['state', 'reason', 'settled_at', 'refunded_by']
                    = to_jsonb(OLD) - ARRAY['state', 'reason', 'settled_at', 'refunded_by'] THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'credit_ledger: change refused for role %', current_user
            USING ERRCODE = 'insufficient_privilege';
    END
    $$"""

# 0007's body, byte for byte, for the downgrade (its digest is what 0007 pinned).
OLD_GUARD = _BODY.replace("__CASCADE__", "")
LEDGER_GUARD = _BODY.replace(
    "-- Every role: the cascade of a deleted job (ON DELETE SET NULL (job_id)), which runs as\n"
    "        -- the table owner.\n"
    "        IF __CASCADE__",
    "-- The table owner only: the cascade of a deleted job (ON DELETE SET NULL (job_id)),\n"
    "        -- which runs as the owner of credit_ledger.\n"
    "        IF current_user = 'classbeat_owner' AND ",
)


def upgrade() -> None:
    op.execute(LEDGER_GUARD)


def downgrade() -> None:
    op.execute(OLD_GUARD)
