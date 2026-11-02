"""Approving the Swedebeat selection (ADR 0016 section 6, B3).

``POST /api/classes/{id}/swedebeat-match`` records ``classes.approved_swedebeat_match_id``: the
``swedebeat_match`` revision the hand-off (B2) and the page (B4) read. The route checks the flag
before it calls ``record``. ``record`` accepts the revision only when it is this class's match of
the current draft's song list (``music.song_list_hash`` equals that list's content hash, and the
match's parent is that list). Anything else is ``not_found`` (no such class or match) or
``stale_match``. The same revision again is a 200 and writes the same id.

Nothing here opens a connection or calls Swedebeat. The approval is a pointer, not a write.
"""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.songs.song_list import load as load_song_list
from classbeat.swedebeat_match.revision import MatchRevision
from classbeat.swedebeat_match.revision import load as load_match

Factory = async_sessionmaker[AsyncSession]


class SelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: UUID


class SelectionOut(BaseModel):
    revision_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


_LOCK = text(
    "SELECT current_revision_id FROM classes WHERE owner_id = :owner_id AND id = :class_id "
    "FOR UPDATE"
)
_LOCK_APPROVED = text(
    "SELECT current_revision_id, approved_swedebeat_match_id FROM classes "
    "WHERE owner_id = :owner_id AND id = :class_id FOR UPDATE"
)
_LIST_OF = text(
    "SELECT music ->> 'song_list_id' AS song_list_id FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND kind = 'draft'"
)
_SET = text(
    "UPDATE classes SET approved_swedebeat_match_id = :match_id, updated_at = now() "
    "WHERE owner_id = :owner_id AND id = :class_id AND current_revision_id = :current_id "
    "RETURNING id"
)


@dataclass(frozen=True, slots=True)
class Approved:
    revision_id: UUID
    content_hash: str  # hex SHA-256 of the match content


@dataclass(frozen=True, slots=True)
class SelectionResult:
    approved: Approved | None
    reason: str | None  # not_found, stale_match


@dataclass(frozen=True, slots=True)
class StoredApproval:
    """The class's stored match, already proven to be the current draft's song list."""

    revision_id: UUID
    current_revision_id: UUID
    match: MatchRevision


def binds(found: MatchRevision, list_id: UUID | None, listed_hash: str | None) -> bool:
    """The match is of this song list: same parent, and both hashes are that list's content.

    Same owner is not enough. ``service.pick`` clears the stored id when the draft it installs
    is not this list, and a reader still requires this for the class's current draft.
    """
    return (
        listed_hash is not None
        and list_id is not None
        and list_id == found.song_list_id
        and listed_hash == found.content.song_list_hash
        and found.music.song_list_hash == found.content.song_list_hash
    )


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


async def belongs_to_draft(
    db: AsyncSession, owner_id: UUID, class_id: UUID, draft_id: UUID, match_id: UUID
) -> bool:
    """Whether ``match_id`` is ``draft_id``'s song list. Same owner is not enough."""
    found = await load_match(db, owner_id, class_id, match_id)
    if found is None:
        return False
    list_id = await _current_list(db, owner_id, class_id, draft_id)
    listed = None if list_id is None else await load_song_list(db, owner_id, class_id, list_id)
    listed_hash = None if listed is None else listed.content_hash.hex()
    return binds(found, list_id, listed_hash)


async def _current_list(
    db: AsyncSession, owner_id: UUID, class_id: UUID, current_id: UUID
) -> UUID | None:
    raw = (
        await db.execute(_LIST_OF, {"owner_id": owner_id, "class_id": class_id, "id": current_id})
    ).scalar_one_or_none()
    return _uuid(raw)


async def record(
    factory: Factory, ctx: UserContext, class_id: UUID, match_id: UUID
) -> SelectionResult:
    """Set the class's approved match, or refuse. The class row is locked for the check."""
    require_context(ctx)
    owner = ctx.user_id
    async with user_session(factory, ctx) as db:
        current = (await db.execute(_LOCK, {"owner_id": owner, "class_id": class_id})).first()
        if current is None or current.current_revision_id is None:
            return SelectionResult(None, "not_found" if current is None else "stale_match")
        found = await load_match(db, owner, class_id, match_id)
        if found is None:
            return SelectionResult(None, "not_found")
        list_id = await _current_list(db, owner, class_id, current.current_revision_id)
        listed = None if list_id is None else await load_song_list(db, owner, class_id, list_id)
        listed_hash = None if listed is None else listed.content_hash.hex()
        if not binds(found, list_id, listed_hash):
            return SelectionResult(None, "stale_match")
        updated = (
            await db.execute(
                _SET,
                {
                    "owner_id": owner,
                    "class_id": class_id,
                    "match_id": match_id,
                    "current_id": current.current_revision_id,
                },
            )
        ).first()
        if updated is None:
            return SelectionResult(None, "stale_match")
        return SelectionResult(Approved(match_id, found.content_hash.hex()), None)


async def stored_approval(
    db: AsyncSession, owner_id: UUID, class_id: UUID
) -> tuple[StoredApproval | None, str | None]:
    """The stored match if it belongs to the current draft's song list.

    Locks the class row. ``not_found`` when the class is not the owner's; ``stale_match`` when
    the column is empty or the match is not that song list (same owner is not enough).
    """
    row = (await db.execute(_LOCK_APPROVED, {"owner_id": owner_id, "class_id": class_id})).first()
    if row is None:
        return None, "not_found"
    match_id = row.approved_swedebeat_match_id
    current_id = row.current_revision_id
    if match_id is None or current_id is None:
        return None, "stale_match"
    found = await load_match(db, owner_id, class_id, match_id)
    if found is None:
        return None, "stale_match"
    list_id = await _current_list(db, owner_id, class_id, current_id)
    listed = None if list_id is None else await load_song_list(db, owner_id, class_id, list_id)
    listed_hash = None if listed is None else listed.content_hash.hex()
    if not binds(found, list_id, listed_hash):
        return None, "stale_match"
    return StoredApproval(match_id, current_id, found), None
