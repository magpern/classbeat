"""The tracks a revised draft had on spotdown's sections (ADR 0040, its SD-P6 stage E amendment;
#1303): ``revise_draft`` and ``refit_draft`` read the base's ``music.spotdown``
(``revision_spotdown_model``) so their music step reads those tracks again
(``songs.spotdown_carry``).

``None``, and so **exactly today's path** (no read at all when inactive): the stage switched off
or the owner not allowlisted (``song_match_config``), a base without the field (every draft before
stage E, one on MOWL's sections only, a mix, a soundless class), a field that does not parse, or
an error of any type here (swallowed; only its type is logged). Only ``RunStopped``,
``LeaseLost`` and ``DatabaseRoleError`` pass. The base is read through the job's own class.
"""

import json
import logging
from typing import Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.db import DatabaseRoleError
from classbeat.design.revision_spotdown_model import SpotdownSections
from classbeat.jobs import JobRun, RunStopped
from classbeat.jobs.errors import LeaseLost
from classbeat.spotdown import song_match_config

logger = logging.getLogger(__name__)

PASSED: Final = (RunStopped, LeaseLost, DatabaseRoleError)

_SECTIONS = text(
    "SELECT r.music -> 'spotdown' AS spotdown FROM jobs j JOIN class_revisions r "
    "ON r.owner_id = j.owner_id AND r.class_id = j.class_id AND r.id = :base "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND r.kind = 'draft'"
)


async def _read(run: JobRun, base_id: UUID) -> SpotdownSections | None:
    if not song_match_config.current().active_for(run.lease.owner_id):
        return None
    async with run.fenced() as fs:
        params = {"base": base_id, "job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
        found = (await fs.db.execute(_SECTIONS, params)).scalar_one_or_none()
    if found is None:
        return None
    try:
        return SpotdownSections.model_validate_json(json.dumps(found))
    except ValidationError:
        return None


async def base_sections(run: JobRun, base_id: UUID) -> SpotdownSections | None:
    """The base's ``music.spotdown``, or ``None`` for today's path (module docstring)."""
    try:
        return await _read(run, base_id)
    except PASSED:
        raise
    except Exception as exc:
        kind = type(exc).__name__
        logger.warning("spotdown base sections failed", extra={"error_type": kind})
        return None
