"""SwedeBeat: the account a connection was made for (ADR 0036 §6, ADR 0035 §3; #1117)

Revision ID: 0057_swedebeat_account_ref
Revises: 0056_spotify_syncs
Create Date: 2026-11-16

- ``connections.account_ref``: a keyed digest of the SwedeBeat ``customer_id`` the gateway's
  ``POST /auth/login`` answered (32 lower-case hex characters,
  ``classbeat.connections.swedebeat_account.account_ref_for``), set on every connect and
  reconnect under the row lock, ``NULL`` when the answer had none. Only ``swedebeat`` rows may
  hold one. It lets a SwedeBeat publish in doubt be checked after a reconnect to the **same**
  account; a row without it counts as another account. The raw ``customer_id`` is never stored.
  It lives and goes with its row (owner RLS already forced on ``connections``): a disconnect, a
  forget and the account's deletion remove it.

The downgrade drops the column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0057_swedebeat_account_ref"
down_revision: str | Sequence[str] | None = "0056_spotify_syncs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.connections ADD COLUMN account_ref text "
        "CONSTRAINT connections_account_ref CHECK (account_ref IS NULL OR "
        "(provider = 'swedebeat' AND account_ref ~ '^[0-9a-f]{32}$'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.connections DROP COLUMN account_ref")
