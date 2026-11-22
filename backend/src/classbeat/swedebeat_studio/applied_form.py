"""B9's facts of the approved studio draft, read in the caller's transaction (ADR 0036 amendment B9,
#1370): ``mixed`` and ``applied_form`` (``studio.mix_applied_policy``), for
``outputs_admit.bound`` at create time and again when the job runs.

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


async def _row(db: AsyncSession, owner_id: UUID, class_id: UUID, revision_id: UUID) -> Any:
    params = {"owner_id": owner_id, "class_id": class_id, "revision_id": revision_id}
    return (await db.execute(_ROW, params)).first()


async def _applied_form(
    db: AsyncSession,
    owner_id: UUID,
    class_id: UUID,
    found: CurrentDraft,
    facts: Mapping[str, TrackFacts],
    outputs: Collection[str],
) -> bool:
    own = await _row(db, owner_id, class_id, found.revision_id)
    provenance: object = own.provenance if own is not None else None
    if not isinstance(provenance, dict):
        return False
    list_id = found.music.song_list_id
    resolved = await mix_proposal.resolve(db, owner_id, class_id, found.revision_id, list_id)
    origin = provenance.get("origin")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    chosen = basis(found.revision_id, origin, resolved, provenance, outputs)  # pyright: ignore[reportUnknownArgumentType]
    if chosen is None:
        return False
    loaded = await mix_proposal.load(db, owner_id, class_id, chosen.proposal_id)
    if loaded is None or loaded.music.song_list_id != list_id:
        return False
    proposal = mix_proposal.proposal_of(loaded, found.songs)
    return holds(found.spec, proposal, facts, chosen.choices)


async def form(
    db: AsyncSession, owner_id: UUID, class_id: UUID, found: CurrentDraft, outputs: Collection[str]
) -> Form:
    """``mixed`` and ``applied_form`` of ``found`` approved for ``outputs`` (module docstring)."""
    facts = await facts_of(db, owner_id, found)
    return Form(
        mixed(found.spec, facts),
        await _applied_form(db, owner_id, class_id, found, facts, outputs),
    )
