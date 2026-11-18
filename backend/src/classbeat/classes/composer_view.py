"""What the class answer says of the composer (ADR 0038 sections 2 and 5, #1188).

- ``composer``: the class was read with the composer flow on (its interpret job, named by the
  stored interpretation, was admitted with ``composer``: true, #1185). False for every other class,
  so the page never guesses from the version history.
- ``fill_songs``: the song fill of the class's **current sketch**, once one was started for it
  (``classbeat.classes.song_fill``): ``{sketch_revision_id, attempt, failure}``; ``failure`` is the
  stored refusal of that attempt, or the way its job ended when it failed (its error category),
  was cancelled (``cancelled``: replaced or cancelled) or expired (``expired``); null while it
  runs or waits. Null when the current revision is no sketch or no fill was started for it.

Every read filters on ``owner_id`` and runs in the caller's ``user_session``. Codes only.
"""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.classes.song_fill_keys import fill_key
from classbeat.design import interpretation_row
from classbeat.design.revision_ready_policy import SKETCH

_STATE = text(
    "SELECT c.current_revision_id, r.provenance ->> 'origin' AS origin FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
_COMPOSER = text(
    "SELECT coalesce(input ->> 'composer' = 'true', false) FROM jobs "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :job_id "
    "AND kind = 'interpret_request'"
)
_JOB_OF_KEY = text(
    "SELECT state, error_category FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND idempotency_key = :key"
)


class FillSongsOut(BaseModel):
    sketch_revision_id: UUID
    attempt: int
    failure: str | None = None


@dataclass(frozen=True, slots=True)
class ComposerState:
    composer: bool = False
    fill_songs: FillSongsOut | None = None


async def composer_state(db: AsyncSession, owner_id: UUID, class_id: UUID) -> ComposerState:
    row = await interpretation_row.read(db, owner_id, class_id)
    if row is None:
        return ComposerState()
    ids = {"owner_id": owner_id, "class_id": class_id}
    composer = bool((await db.execute(_COMPOSER, {**ids, "job_id": row.job_id})).scalar())
    now = (await db.execute(_STATE, ids)).first()
    fill = row.fill
    if (
        now is None
        or now.origin != SKETCH
        or fill is None
        or fill.sketch_revision_id != now.current_revision_id
    ):
        return ComposerState(composer)
    failure = None if fill.refusal is None else fill.refusal.reason
    if failure is None:
        key = fill_key(class_id, fill.sketch_revision_id, fill.attempt)
        job = (await db.execute(_JOB_OF_KEY, {**ids, "key": key})).first()
        if job is not None and job.state == "failed":
            failure = job.error_category or "internal"
        elif job is not None and job.state in ("cancelled", "expired"):
            failure = job.state
    return ComposerState(
        composer,
        FillSongsOut(
            sketch_revision_id=fill.sketch_revision_id, attempt=fill.attempt, failure=failure
        ),
    )
