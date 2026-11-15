"""The studio ``song_list`` revision (ADR 0036 sections 1 and 3): build, store and the re-checking
load. Its content and music are ``song_list_model``'s.

- A ``class_revisions`` row with ``kind = 'song_list'``, immutable (ADR 0007 section 2), approved
  once by its hash (ADR 0008 section 5), stored through the job's fence with the same insert as a
  song pick's list (``songs.song_list.INSERT``: the job's own class, a parent of that class).
- **content_hash** = SHA-256 of the content's canonical JSON (``songs.song_list.content_hash``):
  every reference, duration, BPM, swap and gap, and the order, are under it.
- ``load`` reads one back (owner, class, id and kind together) and returns it only when its stored
  hash is still the SHA-256 of its content, the content and the music parse as a studio list,
  and the fingerprint is still the ordered Spotify ids'. Anything else is None. A song pick's
  list never parses here, and a studio list never parses as a song pick's
  (``songs.song_list.load``: its ``v``, ``source`` and fields are refused there).

Nothing here is logged. The references and the ids never reach a model, an event or a page.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.revision import MAX_CONTENT_BYTES, canonical
from classbeat.jobs import FencedSession, JobFailure
from classbeat.songs.song_list import INSERT, content_hash, fingerprint
from classbeat.swedebeat_studio.song_list_model import StudioListContent, StudioListMusic

_LOAD = text(
    "SELECT content, content_hash, music, provenance, parent_id FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND kind = 'song_list'"
)


@dataclass(frozen=True)
class StudioList:
    """A studio ``song_list`` revision to store (``build``) or as read back (``load``)."""

    content: StudioListContent
    content_hash: bytes
    music: StudioListMusic
    provenance: dict[str, Any]
    parent_id: UUID | None = None

    @property
    def track_ids(self) -> tuple[str, ...]:
        return self.content.track_ids()

    async def store(self, fs: FencedSession) -> UUID:
        """Insert the revision under the job's class and its ``revision_created`` event."""
        revision_id = uuid.uuid4()
        found = (
            await fs.db.execute(
                INSERT,
                {
                    "id": revision_id,
                    "content": _json(self.content.model_dump(mode="json")),
                    "content_hash": self.content_hash,
                    "music": _json(self.music.model_dump(mode="json")),
                    "provenance": json.dumps(self.provenance, sort_keys=True),
                    "parent_id": self.parent_id,
                    "job_id": fs.lease.job_id,
                    "owner_id": fs.lease.owner_id,
                },
            )
        ).first()
        if found is None:  # the job's row or the parent is not what the input said
            raise JobFailure("internal")
        await fs.event(
            "revision_created",
            {"revision_id": revision_id, "kind": "song_list", "number": found.number},
        )
        return revision_id


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def build(
    content: StudioListContent,
    *,
    account: str,
    swedebeat_connection_id: UUID,
    generations: dict[str, int],
    provenance: dict[str, Any],
    parent_id: UUID | None = None,
) -> StudioList:
    """The revision of ``content`` (validated again from its dump), or ``invalid_output`` when it
    breaks a rule of the model or is over 64 KB."""
    try:
        checked = StudioListContent.model_validate(content.model_dump(mode="json"))
        dumped = checked.model_dump(mode="json")
        music = StudioListMusic.model_validate(
            {
                "source": checked.source,
                "fingerprint": fingerprint(checked.track_ids()),
                "spotify_user_id": account,
                "swedebeat_connection_id": str(swedebeat_connection_id),
                "generations": generations,
            }
        )
    except (ValidationError, ValueError):
        raise JobFailure("invalid_output") from None
    if len(canonical(dumped)) > MAX_CONTENT_BYTES:
        raise JobFailure("invalid_output")
    return StudioList(checked, content_hash(dumped), music, provenance, parent_id)


async def load(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID
) -> StudioList | None:
    """The user's studio list of that class, its hash re-checked (see the module docstring)."""
    row = (
        await db.execute(_LOAD, {"owner_id": owner_id, "class_id": class_id, "id": revision_id})
    ).first()
    if row is None or not isinstance(row.content, dict):
        return None
    raw: dict[str, Any] = row.content  # pyright: ignore[reportUnknownVariableType]
    stored = bytes(row.content_hash)
    if content_hash(raw) != stored:
        return None
    try:
        content = StudioListContent.model_validate(raw)
        music = StudioListMusic.model_validate(row.music)
        same = music.fingerprint == fingerprint(content.track_ids())
    except (ValidationError, ValueError):
        return None
    if not same:
        return None
    provenance: dict[str, Any] = row.provenance if isinstance(row.provenance, dict) else {}  # pyright: ignore[reportUnknownVariableType]
    return StudioList(content, stored, music, provenance, row.parent_id)
