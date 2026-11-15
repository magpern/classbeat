"""The outbox of signed events to classbeat-player (#1058, ADR 0034 section 2)

Revision ID: 0050_player_events
Revises: 0049_oidc_provider
Create Date: 2026-11-15

One system table, reached only through ``classbeat.player.outbox``; the app role gets the default
DML, ``classbeat_maint`` its column grants in ``docker/postgres/maint/30-maint.sql`` (the
operator's erase and disable write events). ``classbeat.player.schema`` mirrors it
(``tests/test_player_events_schema.py``).

- ``player_events``: one event (``account_deleted`` or ``sessions_revoked``) for one pairwise
  ``sub``, with its single ``jti``, its retry state and its lease. **No user id and no foreign
  key**: it outlives the user, which is its purpose.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0050_player_events"
down_revision: str | Sequence[str] | None = "0049_oidc_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

B64URL_43 = "{0} ~ '^[A-Za-z0-9_-]{{43}}$'"

UP = (
    f"""
    CREATE TABLE public.player_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind text NOT NULL
            CONSTRAINT player_events_kind CHECK (kind IN ('account_deleted', 'sessions_revoked')),
        sub text NOT NULL CONSTRAINT player_events_sub CHECK ({B64URL_43.format("sub")}),
        jti text NOT NULL UNIQUE CONSTRAINT player_events_jti CHECK ({B64URL_43.format("jti")}),
        created_at timestamptz NOT NULL DEFAULT now(),
        give_up_at timestamptz NOT NULL DEFAULT now() + interval '7 days',
        state text NOT NULL DEFAULT 'pending'
            CONSTRAINT player_events_state CHECK (state IN ('pending', 'failed')),
        attempt integer NOT NULL DEFAULT 0
            CONSTRAINT player_events_attempt CHECK (attempt BETWEEN 0 AND 40),
        available_at timestamptz NOT NULL DEFAULT now(),
        lease_version bigint NOT NULL DEFAULT 0,
        leased_by text CONSTRAINT player_events_leased_by
            CHECK (leased_by ~ '^[A-Za-z0-9._:-]{{1,100}}$'),
        lease_expires_at timestamptz,
        last_error text CONSTRAINT player_events_last_error
            CHECK (last_error ~ '^[a-z0-9_]{{1,32}}$'),
        CONSTRAINT player_events_lease CHECK ((leased_by IS NULL) = (lease_expires_at IS NULL)),
        CONSTRAINT player_events_failed_unleased
            CHECK (state = 'pending' OR leased_by IS NULL),
        CONSTRAINT player_events_lifetime CHECK (give_up_at > created_at)
    )""",
    "CREATE INDEX player_events_claim_idx ON public.player_events (available_at) "
    "WHERE state = 'pending'",
    "CREATE INDEX player_events_sub_idx ON public.player_events (sub)",
    "CREATE INDEX player_events_failed_idx ON public.player_events (give_up_at) "
    "WHERE state = 'failed'",
)

DOWN = ("DROP TABLE public.player_events",)


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
