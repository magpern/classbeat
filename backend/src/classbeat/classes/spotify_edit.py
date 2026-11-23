"""One user edit of a Spotify draft, step 5 onwards of ``classes.user_edit`` (ADR 0042 sections 3
to 5, #1424). Called inside its one transaction, with the class row locked and the base already
checked (current, ready, no open job, under the cap).

1. the draft (``spotify_edit_draft.load``: the one hash re-checked, its overlay and its edit map
   read; a class that is not a Spotify draft with a map is ``edit_unavailable``);
2. an edit that needs a studio piece (a cut pin, an anchor) is ``audio_edit_unavailable``: Spotify
   songs play whole (ADR 0021);
3. the edit (``studio_edit_ops.edited``: the same operations, the overlay's locks honoured,
   ``locked_block_conflict``, ``agent.design.check`` on the draft's own map) with the user's
   **current** plan gates, then the store (``user_edit_store.store_spotify``).

Any refusal stores nothing. Nothing here reads MOWL, Spotify or any network.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.classes import spotify_edit_draft, spotify_edit_state
from classbeat.classes.user_edit_model import EditIn
from classbeat.classes.user_edit_outcome import Outcome
from classbeat.classes.user_edit_store import store_spotify
from classbeat.context import UserContext
from classbeat.design.workout_edit_policy import EditRefused
from classbeat.quota.gates import current_gates
from classbeat.swedebeat_studio.studio_edit_ops import edited

EDIT_UNAVAILABLE = "edit_unavailable"
_UNAVAILABLE = frozenset(
    {
        spotify_edit_draft.NO_SPOTIFY_DRAFT,
        spotify_edit_draft.EDIT_UNAVAILABLE,
        spotify_edit_draft.MAP_TOO_LARGE,  # the page says why (#1453); an edit stays as before
    }
)


async def apply(
    db: AsyncSession, ctx: UserContext, class_id: UUID, current_id: UUID, body: EditIn
) -> Outcome:
    """The edit of the class's current Spotify draft (module docstring)."""
    found = await spotify_edit_draft.load(db, ctx.user_id, class_id)
    if isinstance(found, str):
        return Outcome(EDIT_UNAVAILABLE if found in _UNAVAILABLE else found)
    state = spotify_edit_state.state_of(found)
    if state is None:
        return Outcome(EDIT_UNAVAILABLE)
    audio = spotify_edit_state.refusal(body.edit)
    if audio is not None:
        return Outcome(audio)
    _, gates = await current_gates(db, ctx)
    try:
        made = edited(state, body.edit, gates)
        stored = await store_spotify(
            db,
            ctx.user_id,
            class_id,
            found.revision_id,
            found.music,
            state.map_version,
            made,
            body.edit.op,
        )
    except EditRefused as refused:
        return Outcome(refused.reason)
    if stored is None:  # the row is locked, so this never happens: fail closed
        await db.rollback()
        return Outcome("stale_revision", current_revision_id=current_id)
    return Outcome(stored=stored, made=made)
