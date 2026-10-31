"""A deleted account takes its security events with it (ADR 0014 section 3; #337)

Revision ID: 0015_gdpr_events_cascade
Revises: 0014_plan_route_keys
Create Date: 2026-10-31

``auth_events.user_id`` was ``ON DELETE SET NULL``: the rows of a deleted user stayed for the
30-day purge with their IP address and ``detail``, unlinked from the account but not anonymous
(the address is personal data). They now cascade, for the user's own deletion and the admin's
alike. The one ``account_deleted`` event is written without a user id and without an address.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_gdpr_events_cascade"
down_revision: str | Sequence[str] | None = "0014_plan_route_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP = "ALTER TABLE public.auth_events DROP CONSTRAINT auth_events_user_id_fkey"
_ADD = (
    "ALTER TABLE public.auth_events ADD CONSTRAINT auth_events_user_id_fkey "
    "FOREIGN KEY (user_id) REFERENCES public.users (id) ON DELETE {action}"
)


def upgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(action="CASCADE"))


def downgrade() -> None:
    op.execute(_DROP)
    op.execute(_ADD.format(action="SET NULL"))
