"""What ``classes.user_edit.edit`` answers (shared by its studio and Spotify paths)."""

from dataclasses import dataclass
from uuid import UUID

from classbeat.classes.user_edit_store import Stored
from classbeat.swedebeat_studio.studio_edit_calc import EditResult


@dataclass(frozen=True, slots=True)
class Outcome:
    """``reason`` is ``None`` when the edit was stored."""

    reason: str | None = None
    current_revision_id: UUID | None = None  # with ``stale_revision``
    stored: Stored | None = None
    made: EditResult | None = None
