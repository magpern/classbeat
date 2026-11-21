"""B9's facts of the approved studio draft, read in the caller's transaction (ADR 0036 amendment B9,
#1370): ``mixed`` and ``applied_form`` (``studio.mix_applied_policy``), for
``outputs_admit.bound`` at create time and again when the job runs.

- **The facts** are those of the analyses the draft holds: the user's cached analyses of the
  approved list's references, exactly the rows the draft recorded (``track_cache.cached_in`` with
  ``recorded``), each read as the outputs job reads it (``of_map``, the stored timing class of a
  ``spotdown`` row). A row that is gone or does not read is left out, so the draft is ``mixed``
  and not ``applied_form`` (fail closed).
- **The lineage** is the draft and its parents on the same class of the same user, at most
  ``mix_applied_policy.MAX_CHAIN`` revisions: each one's kind, music source, ``song_list`` and
  origin, then the provenance of the draft itself and the proposal ``basis`` names.
- **The proposal** of ``applied`` is ``Proposal(the approved list by position, the proposal's
  stored spec, the list's target_sec)``, read as #1402's ``mix_proposal.load`` reads it: a studio
  draft whose stored ``content_hash`` still holds over ``{content, mix_spec}``. Any other row, or
  any read that fails, is not ``applied_form``.

Nothing is written or logged.
"""

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design import revision
from classbeat.design.revision import StudioRevisionMusic
from classbeat.design.studio_revision_model import wanted
from classbeat.design.track_cache import cached_in, recorded
from classbeat.spotdown.studio_map_model import stored_timing_class
from classbeat.studio.mix_applied_policy import MAX_CHAIN, Link, basis, holds, mixed
from classbeat.studio.mix_choice_model import Proposal
from classbeat.studio.mix_facts_model import TrackFacts, of_map
from classbeat.swedebeat_studio.current_draft import CurrentDraft

_CHAIN = text(
    "WITH RECURSIVE chain (id, parent_id, kind, source, song_list_id, origin, depth) AS ("
    " SELECT r.id, r.parent_id, r.kind, r.music ->> 'source', r.music ->> 'song_list_id',"
    " r.provenance ->> 'origin', 1"
    " FROM class_revisions r"
    " WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.id = :revision_id"
    " UNION ALL"
    " SELECT p.id, p.parent_id, p.kind, p.music ->> 'source', p.music ->> 'song_list_id',"
    " p.provenance ->> 'origin',"
    " c.depth + 1 FROM chain c JOIN class_revisions p ON p.owner_id = :owner_id"
    " AND p.class_id = :class_id AND p.id = c.parent_id WHERE c.depth < :limit"
    ") SELECT id, kind, source, song_list_id, origin FROM chain ORDER BY depth"
)
_ROW = text(
    "SELECT kind, content, content_hash, music, provenance FROM class_revisions "
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
    params = {
        "owner_id": owner_id,
        "class_id": class_id,
        "revision_id": found.revision_id,
        "limit": MAX_CHAIN,
    }
    chain = [
        Link(r.id, str(r.kind), r.song_list_id, r.origin, r.source)
        for r in (await db.execute(_CHAIN, params)).all()
    ]
    own = await _row(db, owner_id, class_id, found.revision_id)
    provenance: object = own.provenance if own is not None else None
    if not isinstance(provenance, dict):
        return False
    song_list_id = str(found.music.song_list_id)
    chosen = basis(chain, song_list_id, provenance, outputs)  # pyright: ignore[reportUnknownArgumentType]
    if chosen is None:
        return False
    row = await _row(db, owner_id, class_id, chosen.proposal_id)
    if row is None or row.kind != "draft" or not isinstance(row.content, dict):
        return False
    try:
        music = StudioRevisionMusic.model_validate_json(json.dumps(row.music))
    except (ValidationError, TypeError, ValueError):
        return False
    spec = music.mix_spec.model_dump(mode="json")
    if revision.studio_hash(row.content, spec) != bytes(row.content_hash):  # pyright: ignore[reportUnknownArgumentType]
        return False  # its stored hash no longer holds (a revision is immutable: never normal)
    if str(music.song_list_id) != song_list_id:
        return False
    approved = [s.swedebeat.reference if s.swedebeat else None for s in found.songs.content.songs]
    proposal = Proposal(approved, spec, found.songs.content.target_sec)
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
