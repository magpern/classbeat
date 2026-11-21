"""A class's current studio draft as the audition's render plan (ADR 0042 sections 6.4 and 6.5).

``load`` reads, in the caller's transaction (a web request's user session or a job's fenced
session; it writes nothing): the class's current studio draft with its hash re-checked
(``current_draft.load``: ``not_found``, ``no_studio_draft``, ``internal``) and the user's analyses
of its tracks, exactly the rows the draft recorded (``track_cache.cached_in``; one that is gone is
``music_changed``). The plan is ``output_body_policy.render_plan`` of the stored spec with the
analysed lengths, the tracks' facts and the studio's current ``bass_swap`` value: the
**reference-keyed** plan whose SHA-256 is the key's ``plan`` (never the child's plan with paths).

Only stored drafts are auditioned: amendment B's read-only ``mix-choices/preview`` has no audio.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.design.studio_revision_model import wanted
from classbeat.design.track_cache import TrackMap, cached_in, recorded
from classbeat.spotdown.studio_map_model import stored_timing_class
from classbeat.studio import output_body_policy
from classbeat.studio.mix_facts_model import TrackFacts, of_map
from classbeat.swedebeat_studio import config as studio_config
from classbeat.swedebeat_studio import current_draft


@dataclass(frozen=True, slots=True)
class PreviewPlan:
    revision_id: UUID
    plan: list[dict[str, Any]]  # the reference-keyed render plan
    sources: dict[str, str]  # reference -> the analysed bytes' SHA-256
    connection_id: str | None  # the SwedeBeat connection the draft's list was matched with


def track_facts_of(
    maps: Mapping[str, TrackMap], references: Sequence[str]
) -> dict[str, TrackFacts]:
    """Each piece's stored analysis as the mix rules read it (the render's curves, #1330)."""
    out: dict[str, TrackFacts] = {}
    for reference in references:
        found = maps[reference]
        spotdown = found.analyser == "spotdown"
        timing = stored_timing_class(found.provenance) if spotdown else None
        out[reference] = of_map(found.music_map, timing)
    return out


async def load(db: AsyncSession, owner_id: UUID, class_id: UUID) -> PreviewPlan | str:
    """The plan of the class's current studio draft, or a reason (see the module docstring)."""
    found = await current_draft.load(db, owner_id, class_id)
    if isinstance(found, str):
        return found
    references = found.music.mix_spec.references()
    maps = await cached_in(db, owner_id, references, recorded(wanted(found.music, references)))
    if any(r not in maps for r in references):
        return "music_changed"
    durations = {r: float(maps[r].music_map["duration"]) for r in references}
    try:
        plan = output_body_policy.render_plan(
            found.spec,
            durations,
            track_facts_of(maps, references),
            bass_swap=studio_config.current().bass_swap,
        )
    except (ValueError, KeyError, TypeError):
        return "music_changed"  # the cached analyses no longer fit the stored cuts
    return PreviewPlan(
        found.revision_id,
        plan,
        {r: maps[r].sha256 for r in references},
        found.songs.music.swedebeat_connection_id,
    )
