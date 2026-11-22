"""A user edit of the class's current draft, stored as a new ``user_edit`` revision (ADR 0042
sections 3 to 6, #1391). No model runs and no job is queued.

``edit`` runs in **one** transaction as the user, the class row locked (``LOCK_CLASS``, as a pick):

1. ``not_found``: no such class of the user (another user's answers the same);
2. ``stale_revision``: ``base_revision_id`` is not the class's current revision; the answer
   carries the current one (``Outcome.current_revision_id``), so the page reloads it;
3. ``not_ready``: the base is a sketch (``revision_ready``, ADR 0038 section 4);
   ``class_busy``: an open job of the class (``OPEN_JOB_SQL``);
4. ``revision_limit``: the class has ``REVISION_LIMIT`` user-edit revisions already (a clear is
   one; an undo or redo is a pick and stores none);
5. the draft: ``current_draft.load`` (the one hash checked, the overlay read). A class that is not
   a studio class is ``edit_unavailable``: a Spotify draft's composed map needs MOWL's analyses,
   which the web process does not read (reported to the Architect, #1391);
6. its proposal and analyses (``mix_proposal.for_draft``: ``no_proposal``, ``music_changed``) and
   its choices: the basis of its nearest draft that is not a ``user_edit``
   (``mix_applied_policy.basis``, section 8): an applied draft's recorded choices, a proposal's
   DJ-mix or whole-song choices, whichever its stored spec is ``applied`` of with its pins
   (``holds``); none, and an audio edit is ``mix_not_applied``;
7. the edit (``studio_edit_ops.edited``) with the user's **current** plan gates, then the store
   (``user_edit_store``). Any refusal stores nothing.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.classes.class_sql import LOCK_CLASS, OPEN_JOB_SQL
from classbeat.classes.user_edit_model import EditIn
from classbeat.classes.user_edit_store import COUNT_EDITS, REVISION_LIMIT, Stored, store
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.revision_ready import revision_ready
from classbeat.design.workout_edit_policy import EditRefused
from classbeat.quota.gates import current_gates
from classbeat.studio.mix_applied_policy import basis, holds
from classbeat.studio.mix_choice_model import MixChoices
from classbeat.studio.mix_proposal_policy import USER_EDIT_ORIGIN
from classbeat.swedebeat_studio import current_draft, mix_proposal
from classbeat.swedebeat_studio.applied_form import provenance_of
from classbeat.swedebeat_studio.current_draft import CurrentDraft
from classbeat.swedebeat_studio.studio_edit_calc import EditResult, EditState
from classbeat.swedebeat_studio.studio_edit_ops import edited

Factory = async_sessionmaker[AsyncSession]
EDIT_UNAVAILABLE = "edit_unavailable"


@dataclass(frozen=True, slots=True)
class Outcome:
    """``reason`` is ``None`` when the edit was stored."""

    reason: str | None = None
    current_revision_id: UUID | None = None  # with ``stale_revision``
    stored: Stored | None = None
    made: EditResult | None = None


async def _choices(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    found: CurrentDraft,
    resolved: mix_proposal.Resolved,
) -> MixChoices | None:
    """The choices the draft's spec is ``applied`` of (module docstring, step 6), else ``None``."""
    basis_id: UUID | None = found.revision_id
    provenance = await provenance_of(db, owner_id, class_id, found.revision_id)
    if provenance.get("origin") == USER_EDIT_ORIGIN:
        basis_id = await mix_proposal.not_user_edit(db, owner_id, class_id, found.revision_id)
        if basis_id is None:
            return None
        provenance = await provenance_of(db, owner_id, class_id, basis_id)
    origin: object = provenance.get("origin")
    proposal_id = resolved.draft.revision_id
    facts = resolved.music.facts()
    for outputs in (("swedebeat_mix",), ()):
        chosen = basis(basis_id, origin, proposal_id, provenance, outputs)
        if chosen is None or chosen.proposal_id != proposal_id:
            continue
        if holds(found.spec, resolved.proposal, facts, chosen.choices, found.overlay.pins()):
            return chosen.choices
    return None


async def edit(factory: Factory, ctx: UserContext, class_id: UUID, body: EditIn) -> Outcome:
    """One edit of the class's current draft (module docstring)."""
    require_context(ctx)
    owner_id = ctx.user_id
    ids = {"owner_id": owner_id, "class_id": class_id}
    async with user_session(factory, ctx) as db:
        locked = (await db.execute(LOCK_CLASS, ids)).first()
        if locked is None:
            return Outcome("not_found")
        if locked.current_revision_id != body.base_revision_id:
            return Outcome("stale_revision", current_revision_id=locked.current_revision_id)
        not_ready = await revision_ready(db, owner_id, class_id, body.base_revision_id)
        if not_ready is not None:
            return Outcome(not_ready)  # a sketch is never edited (ADR 0038 section 4)
        if (await db.execute(OPEN_JOB_SQL, ids)).first() is not None:
            return Outcome("class_busy")
        if int((await db.execute(COUNT_EDITS, ids)).scalar_one()) >= REVISION_LIMIT:
            return Outcome("revision_limit")
        found = await current_draft.load(db, owner_id, class_id)
        if isinstance(found, str):
            return Outcome(EDIT_UNAVAILABLE if found == "no_studio_draft" else found)
        list_id = found.music.song_list_id
        resolved = await mix_proposal.for_draft(
            db, owner_id, class_id, found.revision_id, list_id, found.songs
        )
        if isinstance(resolved, str):
            return Outcome(resolved)
        state = EditState(
            content=found.content,
            spec=found.spec,
            overlay=found.overlay,
            music=resolved.music,
            analysed={a.reference: a.sha256 for a in found.music.analyses or ()},
            proposal=resolved.proposal,
            choices=await _choices(db, owner_id, class_id, found, resolved),
            map_version=found.music.map_version,
        )
        _, gates = await current_gates(db, ctx)
        try:
            made = edited(state, body.edit, gates)
            stored = await store(
                db, owner_id, class_id, found.revision_id, found.music, made, body.edit.op
            )
        except EditRefused as refused:
            return Outcome(refused.reason)
        if stored is None:  # the row is locked, so this never happens: fail closed
            await db.rollback()
            return Outcome("stale_revision", current_revision_id=locked.current_revision_id)
    return Outcome(stored=stored, made=made)
