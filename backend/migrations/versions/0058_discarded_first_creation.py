"""``jobs.discarded_class_id``: a failed first creation leaves no empty class (ADR 0039)

Revision ID: 0058_discarded_first_creation
Revises: 0057_swedebeat_account_ref
Create Date: 2026-11-17

When the first creation of a class made on the start screen (its ``interpret_request`` job, or the
hand-off's first ``design_draft`` or ``design_with_songs``) ends ``failed`` or ``cancelled`` before
anything was made, ``classbeat.jobs.discard`` deletes the class in the ending's transaction. The
ending job is kept, detached from the class, so its owner can still read why it stopped:

- ``jobs.discarded_class_id``: the id the class had. No foreign key (the class is gone); the
  owner's ``GET /api/classes/{id}`` finds the job by it (``jobs_owner_discarded``).
- ``jobs_class_id`` (migration 0052, widened by 0056 for ``spotify_sync``) also allows a NULL
  ``class_id`` for a detached job, and
  ``jobs_discarded`` holds the shape: a detached job has no class, is one of the three kinds and
  ended ``failed`` or ``cancelled``. ``jobs_one_open_per_class`` never sees it (it is ended).

No new table and no new policy: ``jobs`` already has forced owner row security, and the
application role already updates its rows. The column holds an id, no content; the detached job's
``input`` is emptied when it is detached, so no request text outlives the class
(``classbeat.db.retention``).

Downgrade: deletes the detached jobs (the old check refuses them) with row security lifted for
that statement, as 0052 does; their events go with them, their usage rows stay (``SET NULL``).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0058_discarded_first_creation"
down_revision: str | Sequence[str] | None = "0057_swedebeat_account_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The check as migration 0056 left it (0052 made it, 0056 added ``spotify_sync``; no other
# migration touches it). ``tests/test_jobs_discard_schema.py`` inserts each class-less kind.
OLD_CLASS_CHECK = "class_id IS NOT NULL OR kind IN ('player_remove', 'spotify_sync')"
CLASS_CHECK = f"{OLD_CLASS_CHECK} OR discarded_class_id IS NOT NULL"
DISCARDED_CHECK = (
    "discarded_class_id IS NULL OR (class_id IS NULL "
    "AND kind IN ('interpret_request', 'design_draft', 'design_with_songs') "
    "AND state IN ('failed', 'cancelled'))"
)


def _force_rls(forced: bool) -> str:
    return f"ALTER TABLE public.jobs {'FORCE' if forced else 'NO FORCE'} ROW LEVEL SECURITY"


def upgrade() -> None:
    op.execute("ALTER TABLE public.jobs ADD COLUMN discarded_class_id uuid")
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_class_id")
    op.execute(f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_class_id CHECK ({CLASS_CHECK})")
    op.execute(f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_discarded CHECK ({DISCARDED_CHECK})")
    op.execute(
        "CREATE INDEX jobs_owner_discarded ON public.jobs (owner_id, discarded_class_id) "
        "WHERE discarded_class_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(_force_rls(False))
    op.execute("DELETE FROM public.jobs WHERE discarded_class_id IS NOT NULL")
    op.execute(_force_rls(True))
    op.execute("DROP INDEX public.jobs_owner_discarded")
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_discarded")
    op.execute("ALTER TABLE public.jobs DROP CONSTRAINT jobs_class_id")
    op.execute(f"ALTER TABLE public.jobs ADD CONSTRAINT jobs_class_id CHECK ({OLD_CLASS_CHECK})")
    op.execute("ALTER TABLE public.jobs DROP COLUMN discarded_class_id")
