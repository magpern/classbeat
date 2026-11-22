"""The tracks a revised draft had on spotdown's sections (ADR 0040 amendment "spotdown is the only
analysis path", items 3 and 4, and its SD-P6 stage E amendment; #1303, #1354, #1386, #1407):
``revise_draft`` and ``refit_draft`` read the base's ``music.spotdown``
(``revision_spotdown_model``) so their music step reads those tracks again
(``songs.spotdown_carry``).

**Every Spotify base must have the field** (a pick, an own playlist, the composer, and a
``design_draft`` on the user's MOWL playlist, which keeps its own record, #1407): without it the
base was designed on MOWL's sections (a draft from before the stages; nothing of that kind is
kept, owner 2026-11-21) and is ``music_changed``, never "today's music". ``None`` only for a
**mix** (its own path reads spotdown, ``mix_spotdown``), which reads nothing here. A row that is
not a draft of the job's class, or a field that does not parse, is ``internal``; an error here is
not swallowed. The base is read through the job's own class.
"""

import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.design.revision_spotdown_model import SpotdownSections
from classbeat.jobs import JobFailure, JobRun

_MUSIC = text(
    "SELECT r.music FROM jobs j JOIN class_revisions r "
    "ON r.owner_id = j.owner_id AND r.class_id = j.class_id AND r.id = :base "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND r.kind = 'draft'"
)


async def base_sections(run: JobRun, base_id: UUID, *, mix: bool) -> SpotdownSections | None:
    """The base's ``music.spotdown``; ``None`` on a mix (``mix``) only; ``music_changed`` for a
    Spotify base without it (module docstring)."""
    if mix:
        return None  # a mix's spotdown analysis is its own path, never a carry
    async with run.fenced() as fs:
        params = {"base": base_id, "job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
        music = (await fs.db.execute(_MUSIC, params)).scalar_one_or_none()
    if not isinstance(music, dict):
        raise JobFailure("internal")  # the base was loaded already: it is a draft with music
    found = music.get("spotdown")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if found is None:
        raise JobFailure("music_changed")  # never MOWL's sections (#1386, #1407)
    try:
        return SpotdownSections.model_validate_json(json.dumps(found))
    except ValidationError:
        raise JobFailure("internal") from None
