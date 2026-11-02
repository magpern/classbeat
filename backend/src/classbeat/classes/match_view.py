"""What the class API shows of a ``swedebeat_match`` revision (ADR 0016 section 6; issue #458).

Everything is read in the caller's ``user_session`` and filters on ``owner_id`` itself as well as
through row security, so another user's class or revision is simply not found.

- ``get_match``: one ``swedebeat_match`` revision of the user's own class, read through
  ``classbeat.swedebeat_match.revision.load`` (owner, class, stored hash re-checked, strict
  ``MatchContent``). ``None`` for any other id; ``"unreadable"`` when the row exists but no longer
  checks (the caller answers 500 and shows nothing of it).
- ``is_match``: whether the id is one of the user's own match revisions of that class, reading
  no column of it but its key. Used only while the flag is off, to answer ``feature_off`` for such
  an id without reading its content.
- ``newest_match``: the pointer the class detail carries, the class's newest match revision and
  whether it is the approved selection (``classes.approved_swedebeat_match_id``).

The route picks the fields the page sees one by one (``classes.routes``); nothing here is logged.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.swedebeat_match.revision import MatchContent, load

Factory = async_sessionmaker[AsyncSession]

_ROW = text(
    "SELECT id, number, parent_id, content_hash FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :revision_id "
    "AND kind = 'swedebeat_match'"
)
_EXISTS = text(
    "SELECT 1 FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :revision_id "
    "AND kind = 'swedebeat_match'"
)
_NEWEST = text(
    "SELECT r.id, (r.id IS NOT DISTINCT FROM c.approved_swedebeat_match_id) AS approved "
    "FROM class_revisions r JOIN classes c ON c.owner_id = r.owner_id AND c.id = r.class_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'swedebeat_match' "
    "ORDER BY r.number DESC LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class MatchView:
    id: UUID
    number: int
    parent_id: UUID
    content_hash: str  # hex SHA-256 of the content, what the approval page sends back
    content: MatchContent


@dataclass(frozen=True, slots=True)
class MatchPointer:
    revision_id: UUID
    approved: bool


async def get_match(
    factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID
) -> MatchView | Literal["unreadable"] | None:
    """The user's own match revision of that class; None for any other id; ``"unreadable"`` when
    its stored hash, content or music no longer checks."""
    require_context(ctx)
    params = {"owner_id": ctx.user_id, "class_id": class_id, "revision_id": revision_id}
    async with user_session(factory, ctx) as db:
        row = (await db.execute(_ROW, params)).first()
        if row is None:
            return None
        stored = await load(db, ctx.user_id, class_id, revision_id)
    if (
        stored is None
        or stored.content_hash != bytes(row.content_hash)
        or stored.song_list_id != row.parent_id
    ):
        return "unreadable"
    return MatchView(row.id, row.number, row.parent_id, stored.content_hash.hex(), stored.content)


async def is_match(factory: Factory, ctx: UserContext, class_id: UUID, revision_id: UUID) -> bool:
    require_context(ctx)
    params = {"owner_id": ctx.user_id, "class_id": class_id, "revision_id": revision_id}
    async with user_session(factory, ctx) as db:
        return (await db.execute(_EXISTS, params)).first() is not None


async def newest_match(db: AsyncSession, owner_id: UUID, class_id: UUID) -> MatchPointer | None:
    row = (await db.execute(_NEWEST, {"owner_id": owner_id, "class_id": class_id})).first()
    return None if row is None else MatchPointer(row.id, bool(row.approved))
