"""B9's facts of the approved studio draft, read in the caller's transaction (ADR 0036 amendment B9,
#1370): ``mixed``, ``applied_form`` (``studio.mix_applied_policy``) and ``pin_unconfirmed`` (ADR
0042 section 5, #1390), for ``outputs_admit.bound`` at create time and again when the job runs.

- **The facts** are those of the analyses the draft holds: the user's cached analyses of the
  approved list's references, exactly the rows the draft recorded (``track_cache.cached_in`` with
  ``recorded``), each read as the outputs job reads it (``of_map``, the stored timing class of a
  ``spotdown`` row). A row that is gone or does not read is left out, so the draft is ``mixed``
  and not ``applied_form`` (fail closed).
- **The proposal** is B4's, found by the one shared walk and allowlist
  (``swedebeat_studio.mix_proposal.resolve``, ``studio.mix_proposal_policy``) from the draft
  itself, and read by ``mix_proposal.load`` (a studio draft whose stored ``content_hash`` still
  holds) on the draft's own ``song_list``; ``Proposal`` is ``mix_proposal.proposal_of`` (the
  approved list by position, the proposal's stored spec, the list's ``target_sec``). The choices
  are ``mix_applied_policy.basis``'s, from the draft's origin and provenance. No proposal, another
  list, or any read that fails, is not ``applied_form``. The facts stay the draft's own (above),
  not the proposal's records that the preview and the apply job read (#1369 copies those into an
  applied draft, so the two agree; any difference is refused).
- **The pins.** ``applied_form`` holds the stored spec to ``applied(...)`` with the edit
  overlay's cut pins (``overlay_pins``, the hash-checked ``edit_overlay``; ADR 0042 section 8,
  #1391), so a stored pin without its record is not ``applied_form`` (fail closed).
  ``pin_unconfirmed`` is ``mix_pin_policy.listen_again`` of the stored, hash-checked spec against
  the analysed files the draft recorded (``analyses``; a draft without them knows none, so every
  confirmed pin of it is to be listened to again), and ``requests_moved``: an unconfirmed pin
  whose file is not the one it was set against.
- **A ``user_edit`` draft** (ADR 0042 section 8) is checked with the origin, provenance and so the
  choices of its nearest ancestor that is not a ``user_edit`` (``mix_proposal.not_user_edit``):
  it is admitted when it differs from ``applied(...)`` only by its overlay.

Nothing is written or logged.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.studio_revision_model import wanted
from classbeat.design.track_cache import cached_in, recorded
from classbeat.spotdown.studio_map_model import stored_timing_class
from classbeat.studio.mix_applied_policy import basis, holds, mixed
from classbeat.studio.mix_facts_model import TrackFacts, of_map
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.studio.mix_pin_policy import listen_again, requests_moved
from classbeat.studio.mix_proposal_policy import USER_EDIT_ORIGIN
from classbeat.swedebeat_studio import mix_proposal
from classbeat.swedebeat_studio.current_draft import CurrentDraft

_ROW = text(
    "SELECT provenance FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :revision_id"
)


@dataclass(frozen=True, slots=True)
class Form:
    mixed: bool
    applied_form: bool
    pin_unconfirmed: bool


def overlay_pins(found: CurrentDraft) -> tuple[PinRecord, ...]:
    """The draft's edit-overlay pin records (the overlay ``current_draft.load`` hash-checked)."""
    return found.overlay.pins()


def unconfirmed(found: CurrentDraft) -> bool:
    """A pin of the stored spec to listen to again (module docstring): a confirmed one whose
    analysed file is not the one it was confirmed against, an unconfirmed one whose file is not
    the one it was set against; anything that does not read is one (fail closed)."""
    analysed = {a.reference: a.sha256 for a in found.music.analyses or ()}
    try:
        against = {p.target(): p.set_against for p in found.overlay.pins()}
        moved = requests_moved(found.spec, against, analysed)
        return bool(listen_again(found.spec, analysed)) or bool(moved)
    except (ValueError, KeyError, TypeError):
        return True


async def facts_of(db: AsyncSession, owner_id: UUID, found: CurrentDraft) -> dict[str, TrackFacts]:
    """The approved songs' facts from exactly the analyses the draft recorded (module docstring)."""
    references = [s.swedebeat.reference for s in found.songs.content.songs if s.swedebeat]
    maps = await cached_in(db, owner_id, references, recorded(wanted(found.music, references)))
    out: dict[str, TrackFacts] = {}
    for reference, hit in maps.items():
        timing = stored_timing_class(hit.provenance) if hit.analyser == "spotdown" else None
        try:
            out[reference] = of_map(hit.music_map, timing)
        except (ValueError, KeyError, TypeError):
            continue  # does not read: left out, so the draft is not applied_form
    return out


async def provenance_of(
    db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID
) -> dict[str, Any]:
    """The revision's provenance; ``{}`` for none or one that is no object (fail closed)."""
    params = {"owner_id": owner_id, "class_id": class_id, "revision_id": revision_id}
    found = (await db.execute(_ROW, params)).scalar_one_or_none()
    return found if isinstance(found, dict) else {}  # pyright: ignore[reportUnknownVariableType]


async def _applied_form(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    found: CurrentDraft,
    facts: Mapping[str, TrackFacts],
    outputs: Collection[str],
) -> bool:
    basis_id: UUID | None = found.revision_id
    provenance = await provenance_of(db, owner_id, class_id, found.revision_id)
    if provenance.get("origin") == USER_EDIT_ORIGIN:  # its nearest other draft's (section 8)
        basis_id = await mix_proposal.not_user_edit(db, owner_id, class_id, found.revision_id)
        if basis_id is None:
            return False
        provenance = await provenance_of(db, owner_id, class_id, basis_id)
    if not provenance:
        return False
    list_id = found.music.song_list_id
    resolved = await mix_proposal.resolve(db, owner_id, class_id, found.revision_id, list_id)
    origin = provenance.get("origin")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    chosen = basis(basis_id, origin, resolved, provenance, outputs)  # pyright: ignore[reportUnknownArgumentType]
    if chosen is None:
        return False
    loaded = await mix_proposal.load(db, owner_id, class_id, chosen.proposal_id)
    if loaded is None or loaded.music.song_list_id != list_id:
        return False
    proposal = mix_proposal.proposal_of(loaded, found.songs)
    return holds(found.spec, proposal, facts, chosen.choices, overlay_pins(found))


async def form(
    db: AsyncSession, owner_id: UUID, class_id: UUID, found: CurrentDraft, outputs: Collection[str]
) -> Form:
    """``mixed``, ``applied_form`` and ``pin_unconfirmed`` of ``found`` approved for ``outputs``
    (module docstring)."""
    facts = await facts_of(db, owner_id, found)
    return Form(
        mixed(found.spec, facts),
        await _applied_form(db, owner_id, class_id, found, facts, outputs),
        unconfirmed(found),
    )
