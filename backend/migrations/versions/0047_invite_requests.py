"""Invite requests and invites that carry a tier (issue #985, ADR 0033)

Revision ID: 0047_invite_requests
Revises: 0046_billing
Create Date: 2026-11-12

- ``invite_requests`` (a system table: nobody has an account yet, so there is no ``owner_id`` and
  no row security): what a visitor asked for while sign-up is invite-only, and what an admin
  decided. One pending request per normalized address (``invite_requests_one_open``). Bounded:
  ``heard_from`` at most 300 characters, the address at most 254; the hourly purge deletes a
  decided request 30 days after the decision and a pending one 90 days after it was made.
- ``redeem_codes.invite_id``: the single-use code an approval with a plan makes for its invite
  (``ON DELETE SET NULL``: the invite purge leaves the code, used or not). At most one code per
  invite, and such a code has one use.

Privileges. The app role inserts requests (the public form) and deletes them (the purge); it reads
only ``id``, ``state``, ``created_at`` and ``decided_at`` (the pending count and the purge's
``WHERE``), never the address or the text: guard 2 lists the columns. ``classbeat_maint`` reads
every column and decides (``docker/postgres/maint/30-maint.sql``; guard 11).
``docker/postgres/init/10-roles.sql`` restores the app's grants on a re-run.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0047_invite_requests"
down_revision: str | Sequence[str] | None = "0046_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVITE_REQUESTS = """
    CREATE TABLE public.invite_requests (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL
            CONSTRAINT invite_requests_email CHECK (char_length(email) BETWEEN 3 AND 254),
        email_normalized text NOT NULL CONSTRAINT invite_requests_email_normalized
            CHECK (char_length(email_normalized) BETWEEN 3 AND 254),
        heard_from text NOT NULL DEFAULT ''
            CONSTRAINT invite_requests_heard_from CHECK (char_length(heard_from) <= 300),
        instructor boolean NOT NULL,
        locale text CONSTRAINT invite_requests_locale CHECK (locale ~ '^[a-z]{2}$'),
        state text NOT NULL DEFAULT 'pending' CONSTRAINT invite_requests_state
            CHECK (state IN ('pending', 'issued', 'declined')),
        plan_id text REFERENCES public.plans (id),
        grant_days integer
            CONSTRAINT invite_requests_grant_days CHECK (grant_days BETWEEN 1 AND 366),
        invite_id uuid REFERENCES public.invites (id) ON DELETE SET NULL,
        decided_by uuid REFERENCES public.users (id) ON DELETE SET NULL,
        decided_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT invite_requests_decided CHECK ((state = 'pending') = (decided_at IS NULL)),
        CONSTRAINT invite_requests_tier CHECK (
            (plan_id IS NULL) = (grant_days IS NULL) AND (plan_id IS NULL OR state = 'issued'))
    )"""

UP = (
    INVITE_REQUESTS,
    "CREATE UNIQUE INDEX invite_requests_one_open ON public.invite_requests (email_normalized) "
    "WHERE state = 'pending'",
    "CREATE INDEX invite_requests_state_created ON public.invite_requests (state, created_at)",
    "REVOKE ALL ON TABLE public.invite_requests FROM classbeat_app",
    "GRANT INSERT, DELETE ON TABLE public.invite_requests TO classbeat_app",
    "GRANT SELECT (id, state, created_at, decided_at) ON TABLE public.invite_requests "
    "TO classbeat_app",
    "ALTER TABLE public.redeem_codes ADD COLUMN invite_id uuid "
    "REFERENCES public.invites (id) ON DELETE SET NULL",
    "CREATE UNIQUE INDEX redeem_codes_invite_id ON public.redeem_codes (invite_id)",
    "ALTER TABLE public.redeem_codes ADD CONSTRAINT redeem_codes_invite_single_use "
    "CHECK (invite_id IS NULL OR max_redemptions = 1)",
)

DOWN = (
    "ALTER TABLE public.redeem_codes DROP CONSTRAINT redeem_codes_invite_single_use",
    "DROP INDEX public.redeem_codes_invite_id",
    "ALTER TABLE public.redeem_codes DROP COLUMN invite_id",
    "DROP TABLE public.invite_requests",
)


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
