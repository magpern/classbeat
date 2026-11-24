"""What the class answer says of the composer (ADR 0038 sections 2 and 5, #1188).

- ``composer``: the class was read with the composer flow on (its interpret job, named by the
  stored interpretation, was admitted with ``composer``: true, #1185). False for every other class,
  so the page never guesses from the version history. Once the hand-off ran, the route it took
  decides (``interpretation.route``, #1484): ``sketch`` is true whatever the plan's coverage is
  now, ``classic`` (the sketch was not available at hand-off, so the class took the older route)
  is false; a row with no route (stored before, or not handed off yet) follows the job.
  **Reading window** (#1206): while the class
  has no interpretation yet, it is true only while its ``interpret_request`` job is still queued or
  running, was admitted with ``composer``: true and a music choice other than "No music", **and**
  the ``composer_flow`` gate is on now; the workspace then shows from the first second. A job that
  ended without storing a reading (failed, cancelled, expired) gives false again, as before.
- ``fill_songs``: the song fill of the class's **current sketch**, once one was started for it
  (``classbeat.classes.song_fill``): ``{sketch_revision_id, attempt, failure}``; ``failure`` is the
  stored refusal of that attempt, or the way its job ended when it failed (its error category),
  was cancelled (``cancelled``: replaced or cancelled) or expired (``expired``); null while it
  runs or waits, and while a swap or change that replaced it runs or waits. Null when the
  current revision is no sketch or no fill was started for it (no job of the attempt and no
  stored refusal).

- ``save_pending`` (ADR 0038 section 7, #1189): ``{revision_id, content_hash, job_id}`` of the
  draft phase 2 stored for Approve and save, while it counts (``saving.save_pending.current``: it
  names the current and newest revision); the page saves exactly that revision. Null otherwise.
- ``phase2`` (#1189): ``{state: "failed"}`` while the current sketch's approved list can be resumed
  (**Create the playlist again**: the newest list's composer job was approved and ended without
  completing, the receipt is still valid, ``songs.admit.receipt_valid``, and its sketch is the
  current revision); ``{state: "differed"}`` while the current revision is a phase-2 draft whose
  final songs differed from the approved ones (``provenance.fit = "redesigned"``: re-designed, not
  saved by itself; the page asks the user to check the class and save). Null otherwise.

Every read filters on ``owner_id`` and runs in the caller's ``user_session``. Codes only.
"""

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.classes.song_fill_keys import fill_key
from classbeat.design import interpretation_row
from classbeat.design.composer import composer_on
from classbeat.design.revision_ready_policy import SKETCH
from classbeat.saving import save_pending
from classbeat.songs.admit import RECEIPT_ENDED, receipt_valid
from classbeat.songs.inputs import COMPOSER_MODES

_STATE = text(
    "SELECT c.current_revision_id, r.provenance ->> 'origin' AS origin, "
    "r.provenance ->> 'fit' AS fit FROM classes c "
    "LEFT JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id "
    "AND r.id = c.current_revision_id WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
# The class's reading while it runs: a composer job (admitted with ``composer``: true, and not for
# "No music", which has no sketch) that is queued or running, before any interpretation is stored.
_READING = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'interpret_request' AND state IN ('queued', 'running') "
    "AND coalesce(input ->> 'composer' = 'true', false) "
    "AND coalesce(input #>> '{music_override,source}', '') <> 'none' LIMIT 1"
)
_COMPOSER = text(
    "SELECT coalesce(input ->> 'composer' = 'true', false) FROM jobs "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :job_id "
    "AND kind = 'interpret_request'"
)
# A composer song job of the class still running or waiting: a swap or change replacing the fill.
_LIVE_REPLACEMENT = text(
    "SELECT 1 FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'design_with_songs' AND input ->> 'mode' IN ('swap', 'change') "
    "AND state IN ('queued', 'running', 'waiting_for_approval') LIMIT 1"
)
_JOB_OF_KEY = text(
    "SELECT state, error_category FROM jobs WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND idempotency_key = :key"
)


# The class's newest song list and the job that stored it (``songs.admit``'s receipt rule).
_NEWEST_LIST = text(
    "SELECT r.id, j.state, j.approved_at, j.input ->> 'mode' AS mode, "
    "j.input ->> 'base_revision_id' AS base FROM class_revisions r "
    "JOIN jobs j ON j.owner_id = r.owner_id AND j.id = r.job_id "
    "WHERE r.owner_id = :owner_id AND r.class_id = :class_id AND r.kind = 'song_list' "
    "AND j.kind = 'design_with_songs' ORDER BY r.number DESC LIMIT 1"
)


class FillSongsOut(BaseModel):
    sketch_revision_id: UUID
    attempt: int
    failure: str | None = None


class SavePendingOut(BaseModel):
    revision_id: UUID
    content_hash: str
    job_id: UUID


class Phase2Out(BaseModel):
    state: Literal["failed", "differed"]


@dataclass(frozen=True, slots=True)
class ComposerState:
    composer: bool = False
    fill_songs: FillSongsOut | None = None
    save_pending: SavePendingOut | None = None
    phase2: Phase2Out | None = None

    def fields(self) -> dict[str, Any]:
        """The class answer's composer fields (``ClassDetailOut``)."""
        names = ("composer", "fill_songs", "save_pending", "phase2")
        return {name: getattr(self, name) for name in names}


async def composer_state(db: AsyncSession, owner_id: UUID, class_id: UUID) -> ComposerState:
    row = await interpretation_row.read(db, owner_id, class_id)
    ids = {"owner_id": owner_id, "class_id": class_id}
    if row is None:  # no reading stored yet; ``save_pending`` follows its own rule
        return await _after_phase_two(db, owner_id, class_id, await _reading(db, ids), None)
    composer = row.route != "classic" and (
        row.route == "sketch"
        or bool((await db.execute(_COMPOSER, {**ids, "job_id": row.job_id})).scalar())
    )
    now = (await db.execute(_STATE, ids)).first()
    if now is not None and now.origin != SKETCH:
        return await _after_phase_two(db, owner_id, class_id, composer, now.fit)
    fill = row.fill
    if (
        now is None
        or now.origin != SKETCH
        or fill is None
        or fill.sketch_revision_id != now.current_revision_id
    ):
        return ComposerState(composer)
    phase2 = await _failed(db, owner_id, class_id, now.current_revision_id)
    failure = None if fill.refusal is None else fill.refusal.reason
    if failure is None:
        key = fill_key(class_id, fill.sketch_revision_id, fill.attempt)
        job = (await db.execute(_JOB_OF_KEY, {**ids, "key": key})).first()
        if job is None:  # recorded, but no job and no refusal (a transient class_busy): none
            return ComposerState(composer)
        if job.state == "failed":
            failure = job.error_category or "internal"
        elif job is not None and job.state in ("cancelled", "expired"):
            live = (await db.execute(_LIVE_REPLACEMENT, ids)).first() is not None
            failure = None if live else job.state  # a swap or change of it runs or waits
    return ComposerState(
        composer,
        FillSongsOut(
            sketch_revision_id=fill.sketch_revision_id, attempt=fill.attempt, failure=failure
        ),
        phase2=phase2,
    )


async def _reading(db: AsyncSession, ids: dict[str, UUID]) -> bool:
    """The reading window (#1206): the composer's reading of this class runs and the flow is on.
    The job is read first, so a class that was never read (every old one) costs no flag read."""
    if (await db.execute(_READING, ids)).first() is None:
        return False
    return await composer_on(db)


async def _after_phase_two(
    db: AsyncSession, owner_id: UUID, class_id: UUID, composer: bool, fit: str | None
) -> ComposerState:
    """A draft is current: the save phase 2 asked for, and whether its songs differed."""
    pending = await save_pending.current(db, owner_id, class_id)
    return ComposerState(
        composer,
        save_pending=None if pending is None else SavePendingOut(**pending.model_dump()),
        phase2=Phase2Out(state="differed") if fit == "redesigned" else None,
    )


async def _failed(
    db: AsyncSession, owner_id: UUID, class_id: UUID, sketch: UUID
) -> Phase2Out | None:
    """``failed`` while the newest list's approval of this sketch can be resumed."""
    ids = {"owner_id": owner_id, "class_id": class_id}
    newest = (await db.execute(_NEWEST_LIST, ids)).first()
    if newest is None or newest.mode not in COMPOSER_MODES or newest.base != str(sketch):
        return None
    if newest.state not in RECEIPT_ENDED or newest.approved_at is None:
        return None
    found = await receipt_valid(db, owner_id, class_id, newest.id)
    return None if found is None else Phase2Out(state="failed")
