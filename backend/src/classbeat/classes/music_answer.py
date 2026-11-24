"""The composer's question about the music: ``PUT /api/classes/{id}/music`` and the decision as
the class answer shows it (ADR 0038 amendment of 2026-11-24, decision 5, #1495).

``answer`` runs in one transaction of the caller's ``user_session``:

1. ``not_found`` unless the class is the user's (another user's class is answered as an unknown
   one);
2. ``feature_off`` while the operator's composer pair is off (decision 3: the ride is paused);
3. the class row is locked with its stored interpretation (``interpretation_row.read``, ``lock``),
   and only then is the rest read: ``music_fixed`` unless the class has a stored reading, its
   current revision is a sketch (only a composer class has one) and **no fill was started for
   that sketch** (no ``design_with_songs`` job in mode ``fill`` on it, in any state). The song
   fill's create locks the same row (``song_fill._Hold``) and compares the decision it planned
   with the stored one, so an answer either lands before the fill reads it or is refused
   ``music_fixed``;
4. the decision (``music_decision.answered``) is stored when it differs from the stored one, so
   the same answer twice changes nothing (idempotent per state).

No Spotify, MOWL or model call, no job and no credit: the answer only says what the song search
will do. The shared playlists are checked for their shape and count only (``music_sources``); the
song search reads each with the user's own Spotify connection. Every statement filters on
``owner_id`` as well as running under RLS. The note is never logged or answered back.

``decision_out``: the class answer's ``music_decision`` (``ClassDetailOut``): the state, the
choice and how many playlists the song search will favour (the shared ones, else the request's
attached ones), never an id or the note.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design import composer, interpretation_row, music_decision
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.design.music_decision import MusicAnswer
from classbeat.design.revision_ready_policy import SKETCH

Factory = async_sessionmaker[AsyncSession]

_EXISTS = text("SELECT 1 FROM classes WHERE owner_id = :owner_id AND id = :class_id")
_CURRENT = text(
    "SELECT c.current_revision_id, r.provenance ->> 'origin' AS origin FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
# A fill of this sketch was started: its job exists, whatever its state now.
_FILL_STARTED = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'design_with_songs' AND input ->> 'mode' = 'fill' "
    "AND input ->> 'base_revision_id' = :sketch LIMIT 1"
)
_ATTACHED = text(
    "SELECT input -> 'music_sources' FROM jobs WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND id = :job_id AND kind = 'interpret_request'"
)


class MusicDecisionOut(BaseModel):
    state: Literal["undecided", "decided"]
    choice: Literal["request", "pick", "playlists", "describe"] | None = None
    playlists: int = 0  # how many playlists the song search favours


async def answer(
    factory: Factory, ctx: UserContext, class_id: UUID, given: MusicAnswer
) -> MusicDecisionOut | str:
    """The stored decision, or the refusal (``not_found``, ``feature_off``, ``music_fixed``)."""
    require_context(ctx)
    ids: dict[str, Any] = {"owner_id": ctx.user_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        if (await db.execute(_EXISTS, ids)).first() is None:
            return "not_found"
        if not await composer.composer_on(db):
            return "feature_off"
        row = await interpretation_row.read(db, ctx.user_id, class_id, lock=True)
        now = (await db.execute(_CURRENT, ids)).first()
        if row is None or now is None or now.origin != SKETCH:
            return music_decision.FIXED
        sketch = str(now.current_revision_id)
        if (await db.execute(_FILL_STARTED, {**ids, "sketch": sketch})).first() is not None:
            return music_decision.FIXED
        decision = music_decision.answered(given)
        if decision != music_decision.decision_of(row.music_decision):
            stored = row.model_copy(update={"music_decision": decision})
            await interpretation_row.write(db, ctx.user_id, class_id, stored)
            row = stored
        return await decision_out(db, ctx.user_id, class_id, row)


async def decision_out(
    db: AsyncSession, owner_id: UUID, class_id: UUID, row: StoredInterpretation
) -> MusicDecisionOut:
    """The class answer's view of the decision (``music_decision.fill_sources``' count)."""
    decision = music_decision.decision_of(row.music_decision)
    count = len(decision.sources)
    if decision.choice != "playlists":  # the request's attached playlists (validated at admit)
        params = {"owner_id": owner_id, "class_id": class_id, "job_id": row.job_id}
        raw = (await db.execute(_ATTACHED, params)).scalar_one_or_none()
        count = len(raw) if isinstance(raw, list) else 0  # pyright: ignore[reportUnknownArgumentType]
    return MusicDecisionOut(state=decision.state, choice=decision.choice, playlists=count)
