"""``revision.adopt_first`` (ADR 0007 section 2): the class's first design adopted in the job's
final fenced transaction. Moved out of ``classbeat.design.revision`` unchanged (#1303, the
code-structure rule "split on touch"); ``revision`` re-exports it.
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from classbeat.jobs import FencedSession

# The job's own class (read from the fenced job row, never from the input). All three are set
# together and only while the class has no current revision: its first design.
_ADOPT_FIRST = text(
    "UPDATE classes c SET current_revision_id = :revision_id, brief = CAST(:brief AS jsonb), "
    "music_source = CAST(:music_source AS jsonb) FROM jobs j "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.owner_id = j.owner_id "
    "AND c.id = j.class_id AND c.current_revision_id IS NULL RETURNING c.id"
)


async def adopt_first(
    fs: FencedSession, revision_id: UUID, brief: dict[str, Any], music_source: dict[str, Any]
) -> bool:
    """In the final fenced transaction, after the revision is stored: make it the class's current
    revision and record the brief and the music source, if the class has none yet. Returns
    whether it did (a class that already has a current revision keeps it)."""
    found = await fs.db.execute(
        _ADOPT_FIRST,
        {
            "revision_id": revision_id,
            "brief": json.dumps(brief, sort_keys=True, ensure_ascii=False),
            "music_source": json.dumps(music_source, sort_keys=True, ensure_ascii=False),
            "job_id": fs.lease.job_id,
            "owner_id": fs.lease.owner_id,
        },
    )
    return found.first() is not None
