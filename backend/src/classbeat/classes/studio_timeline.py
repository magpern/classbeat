"""The page's music timeline of a class's current studio draft (ADR 0036 section 5, SR C6a).

``view`` reads, in one user transaction: the studio on (``feature_off`` otherwise), the class's
current studio draft with its hash re-checked (``current_draft.load``: ``not_found``,
``no_studio_draft``, ``internal``) and the user's cached analyses of the draft's references
(``track_cache.cached_in``, exactly the rows the draft recorded; one that is gone is
``music_changed``: Change songs analyses again).
The timeline itself is ``timeline_calc``'s, from ``compose_map`` of the stored mix spec.

The answer names the draft (``revision_id``, ``content_hash``: what the output choice binds) and
the songs by the approved list's SwedeBeat title, artist and BPM. **No catalogue reference and no
Spotify id** leave the server. It adds the mix-choice fields of ``studio_timeline_schemas``
(#1369): per track its analysed length and timing class, and the draft's spec digest, its
proposal (``mix_proposal.resolve``), whether it is plain and an applied draft's recorded choices.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.classes.studio_timeline_schemas import DraftMix, TrackMix
from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.studio_revision_model import wanted
from classbeat.design.track_cache import TrackMap, cached_in, recorded
from classbeat.spotdown.studio_map_model import stored_timing_class
from classbeat.studio.mix_choice_policy import is_plain
from classbeat.studio.mix_proposal_policy import APPLIED_ORIGIN
from classbeat.studio.timeline_calc import SongFacts, timeline
from classbeat.swedebeat_studio import current_draft, mix_proposal
from classbeat.swedebeat_studio.admit import studio_on
from classbeat.swedebeat_studio.mix_apply_calc import spec_digest

Factory = Any

_PROVENANCE = text(
    "SELECT provenance FROM class_revisions "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id"
)


def _track_mix(found: TrackMap) -> dict[str, Any]:
    timing = stored_timing_class(found.provenance) if found.analyser == "spotdown" else None
    return TrackMix(length_sec=float(found.music_map["duration"]), timing_class=timing).model_dump(
        mode="json"
    )


async def _draft_mix(
    db: AsyncSession, owner_id: UUID, class_id: UUID, found: current_draft.CurrentDraft
) -> dict[str, Any]:
    ids = {"owner_id": owner_id, "class_id": class_id}
    list_id = found.music.song_list_id
    proposal = await mix_proposal.resolve(db, owner_id, class_id, found.revision_id, list_id)
    made = (await db.execute(_PROVENANCE, {**ids, "id": found.revision_id})).scalar_one_or_none()
    recorded_choices: Any = None
    if isinstance(made, dict) and made.get("origin") == APPLIED_ORIGIN:  # pyright: ignore[reportUnknownMemberType]
        recorded_choices = made.get("choices")  # pyright: ignore[reportUnknownMemberType]
    return {
        "spec_digest": spec_digest(found.spec),
        "proposal_revision_id": None if proposal is None else str(proposal),
        "choices": recorded_choices if isinstance(recorded_choices, dict) else None,
    }


async def view(factory: Factory, ctx: UserContext, class_id: UUID) -> dict[str, Any] | str:
    """The timeline, or a reason (see the module docstring)."""
    require_context(ctx)
    async with user_session(factory, ctx) as db:
        if not await studio_on(db):
            return "feature_off"
        found = await current_draft.load(db, ctx.user_id, class_id)
        if isinstance(found, str):
            return found
        references = found.music.mix_spec.references()
        maps = await cached_in(  # exactly the analyses the draft was designed on
            db, ctx.user_id, references, recorded(wanted(found.music, references))
        )
        mix = await _draft_mix(db, ctx.user_id, class_id, found)
    if any(r not in maps for r in references):
        return "music_changed"
    by_reference = {
        s.swedebeat.reference: s.swedebeat for s in found.songs.content.songs if s.swedebeat
    }
    if any(r not in by_reference for r in references):
        return "internal"  # each piece of the mix is a track of the approved list
    facts = [
        SongFacts(by_reference[r].title, by_reference[r].artist, by_reference[r].bpm)
        for r in references
    ]
    try:
        made = timeline(found.spec, [maps[r].music_map for r in references], facts, found.content)
    except (ValueError, KeyError, TypeError):
        return "music_changed"  # the cached analyses no longer fit the stored cuts
    for track, reference in zip(made["tracks"], references, strict=True):
        track.update(_track_mix(maps[reference]))
    lengths = {r: float(maps[r].music_map["duration"]) for r in references}
    draft = DraftMix(is_plain=is_plain(found.spec, lengths), **mix).model_dump(mode="json")
    return {
        "revision_id": str(found.revision_id),
        "content_hash": found.content_hash.hex(),
        **made,
        **draft,
    }
