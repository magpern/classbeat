"""The tracks a revised draft had on spotdown's sections (ADR 0040 amendment "spotdown is the only
analysis path", item 4, and its SD-P6 stage E amendment; #1303, #1354): ``revise_draft`` and
``refit_draft`` read the base's ``music.spotdown`` (``revision_spotdown_model``) so their music step
reads those tracks again (``songs.spotdown_carry``).

``None`` (the step as it is, today's music for a base made without the stages: a draft from an own
playlist, a mix, a soundless class, or one without the field): the base has no field. A field that
does not parse is ``internal``, and an error here is not swallowed. The base is read through the
job's own class.
"""

import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.design.revision_spotdown_model import SpotdownSections
from classbeat.jobs import JobFailure, JobRun

_SECTIONS = text(
    "SELECT r.music -> 'spotdown' AS spotdown FROM jobs j JOIN class_revisions r "
    "ON r.owner_id = j.owner_id AND r.class_id = j.class_id AND r.id = :base "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND r.kind = 'draft'"
)


async def base_sections(run: JobRun, base_id: UUID) -> SpotdownSections | None:
    """The base's ``music.spotdown``, or ``None`` when the base has none (module docstring)."""
    async with run.fenced() as fs:
        params = {"base": base_id, "job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
        found = (await fs.db.execute(_SECTIONS, params)).scalar_one_or_none()
    if found is None:
        return None
    try:
        return SpotdownSections.model_validate_json(json.dumps(found))
    except ValidationError:
        raise JobFailure("internal") from None
