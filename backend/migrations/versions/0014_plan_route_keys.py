"""Each plan has a route key of its own (ADR 0011 section 6, AD5 follow-up of #223)

Revision ID: 0014_plan_route_keys
Revises: 0013_spotify_quota
Create Date: 2026-10-31

``plans.route_key`` was ``'default'`` for every plan (migration 0007), so a model route applied to
every plan alike. It now is the plan's own id (``free``, ``instructor``, ``pro``): a route set for
``pro`` (the models CLI or the admin API) applies to the jobs of a user whose effective plan is
``pro`` and to no other. A plan without a route for a stage group uses the bootstrap choice (ADR
0012 section 5). A route that exists for ``default`` no longer covers any seeded plan.

No grant changes: ``classbeat_maint`` still cannot update ``plans.route_key``. The downgrade sets
every plan back to ``'default'``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_plan_route_keys"
down_revision: str | Sequence[str] | None = "0013_spotify_quota"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A plan id (^[a-z_]{1,30}$) always fits the route key check (^[a-z0-9_]{1,40}$).
    op.execute("UPDATE public.plans SET route_key = id")


def downgrade() -> None:
    op.execute("UPDATE public.plans SET route_key = 'default'")
