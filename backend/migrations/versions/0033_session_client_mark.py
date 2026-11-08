"""The client mark of a session (issue #632, ADR 0003 amendment of 2026-11-05)

Revision ID: 0033_session_client_mark
Revises: 0032_analysis_slot
Create Date: 2026-11-05

``sessions.seen_ip_hash`` (the HMAC of the coarse client network, ``session-client`` key) and
``sessions.seen_agent`` (the browser family) are what the session was last seen from;
``sessions.location_changed_at`` is when they last changed and ``sessions.location_event_at`` when
the last ``session_location_changed`` event was written (one per session and minute, decided in
the same conditional update). All four are nullable: an existing
session has no mark and takes the mark of its next request silently. The downgrade drops them;
sessions keep working without (they are only compared while the columns exist).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033_session_client_mark"
down_revision: str | Sequence[str] | None = "0032_analysis_slot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.sessions ADD COLUMN seen_ip_hash bytea, "
        "ADD COLUMN seen_agent text, ADD COLUMN location_changed_at timestamptz, "
        "ADD COLUMN location_event_at timestamptz"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.sessions DROP COLUMN seen_ip_hash, DROP COLUMN seen_agent, "
        "DROP COLUMN location_changed_at, DROP COLUMN location_event_at"
    )
