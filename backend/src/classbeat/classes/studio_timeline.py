"""The page's music timeline of a class's current studio draft (ADR 0036 section 5, SR C6a).

``view`` reads, in one user transaction: the studio on (``feature_off`` otherwise), the class's
current studio draft with its hash re-checked (``current_draft.load``: ``not_found``,
``no_studio_draft``, ``internal``) and the user's cached analyses of the draft's references
(``track_cache.cached_in``, exactly the rows the draft recorded; one that is gone is
``music_changed``: Change songs analyses again).
The timeline itself is ``timeline_calc``'s, from ``compose_map`` of the stored mix spec.

The answer names the draft (``revision_id``, ``content_hash``: what the output choice binds) and
the songs by the approved list's SwedeBeat title, artist and BPM. **No catalogue reference and no
Spotify id** leave the server.
"""

from typing import Any
from uuid import UUID

from classbeat.context import UserContext, require_context
from classbeat.db import user_session
from classbeat.design.studio_revision_model import wanted
from classbeat.design.track_cache import cached_in, recorded
from classbeat.studio.timeline_calc import SongFacts, timeline
from classbeat.swedebeat_studio import current_draft
from classbeat.swedebeat_studio.admit import studio_on

Factory = Any


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
    if any(r not in maps for r in references):
        return "music_changed"
    by_reference = {
        s.swedebeat.reference: s.swedebeat for s in found.songs.content.songs if s.swedebeat
    }
    if any(r not in by_reference for r in references):
        return "internal"  # the draft's mix is a permutation of its approved list
    facts = [
        SongFacts(by_reference[r].title, by_reference[r].artist, by_reference[r].bpm)
        for r in references
    ]
    try:
        made = timeline(found.spec, [maps[r].music_map for r in references], facts, found.content)
    except (ValueError, KeyError, TypeError):
        return "music_changed"  # the cached analyses no longer fit the stored cuts
    return {"revision_id": str(found.revision_id), "content_hash": found.content_hash.hex(), **made}
