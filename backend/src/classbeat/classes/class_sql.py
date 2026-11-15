"""SQL shared by the class delete (``removal``) and the draft pick (``service``): the class row
lock and the open-job check."""

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
