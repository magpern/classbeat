"""The answer of ``GET /api/jobs/{id}/revision`` (U1, #93), split out of ``classbeat.jobs.routes``
(CLAUDE.md, split on touch): the job's newest draft revision as the page shows it, read back with
the class's sport's schema (``agent.stored_draft``), and the tracks MOWL had no BPM for. Pure: no
database (the route reads the row with ``classbeat.jobs.progress.read_revision``).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from classbeat.agent.stored_draft import stored_draft
from classbeat.jobs.progress import StoredRevision


class UnknownBpmOut(BaseModel):
    index: int
    title: str


class RevisionOut(BaseModel):
    revision_id: UUID
    number: int
    kind: str
    content: dict[str, Any]
    unknown_bpm: list[UnknownBpmOut]
    created_at: datetime


def revision_out(stored: StoredRevision) -> RevisionOut | None:
    """The answer, or None when the stored row does not read back (the route's 500)."""
    try:
        draft = stored_draft(stored.content, stored.sport)  # the class's sport's schema
        raw: Any = stored.music.get("unknown_bpm", [])
        unknown = [UnknownBpmOut(index=int(u["index"]), title=str(u["title"])) for u in raw]
    except (ValidationError, KeyError, TypeError, ValueError):
        return None
    return RevisionOut(
        revision_id=stored.id,
        number=stored.number,
        kind=stored.kind,
        content=draft.model_dump(mode="json", exclude_none=True),
        unknown_bpm=unknown,
        created_at=stored.created_at,
    )
