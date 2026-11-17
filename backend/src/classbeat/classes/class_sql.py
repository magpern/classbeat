"""SQL shared by the class delete (``removal``), the draft pick (``service``) and the discard of a
failed first creation (``classbeat.jobs.discard``): the class row lock, the open-job check and the
delete."""

from typing import Final

from sqlalchemy import text

OPEN_JOB: Final = "('queued', 'running', 'waiting_for_approval')"

LOCK_CLASS = text(
    "SELECT current_revision_id, approved_swedebeat_match_id FROM classes "
    "WHERE id = :class_id AND owner_id = :owner_id FOR UPDATE"
)
OPEN_JOB_SQL = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "  # noqa: S608 - constants only
    f"AND state IN {OPEN_JOB} LIMIT 1"
)
DELETE_CLASS = text("DELETE FROM classes WHERE id = :class_id AND owner_id = :owner_id")
