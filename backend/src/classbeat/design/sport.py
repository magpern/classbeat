"""A class's sport at the edges of the jobs (ADR 0018 sections 1, 2 and 5; M8 R1a).

The sport is a key of the validated brief (``Brief.sport``, absent = cycling). This module holds
the checks that are not the brief's own validation:

- **The flag.** ``running_enabled`` is the environment's kill switch
  (``CLASSBEAT_RUNNING_ENABLED``, off by default; ``SportGate.running_enabled``, set by the web
  app and the worker at start) and the admin flag (``classbeat.flags``, stricter wins). Off, a
  new running design (``admit_brief``) and a revise of a running class (``admit_class``) are
  refused ``feature_off`` and write nothing, and a queued running job ends ``feature_off`` when
  the worker runs it (``still_on``), before any model or MOWL call. Cycling never reads it.
- **Immutable once a revision exists** (section 1). A design job on an existing class whose
  first revision exists (or whose brief is stored) is refused ``invalid_input`` when its brief's
  sport is not the class's (``admit_brief``); the final store checks it again in its fenced
  transaction (``check_stored``), so two queued jobs of different sports cannot both land.
- **Running v1 designs on an existing MOWL playlist only** (section 2, Amendment 2): no Spotify
  song picking and no Swedebeat mix. The input models refuse those combinations
  (``DesignDraftInput``, ``DesignWithSongsInput``), and the song kinds refuse a running brief
  again in the worker.

- **An evaluated model** (ADR 0012 amendment of 2026-11-03, issue #465). A running job runs only
  on a model whose recorded suite result covers running (``GroupModel.running_covered``: a
  ``design`` route that is not stale, never the bootstrap choice); otherwise
  ``require_evaluated`` ends it ``model_config`` before any MOWL or model call.

Without a configured gate the switch is off, so running is refused everywhere.
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.schemas import Brief
from classbeat.flags import effective
from classbeat.jobs import FencedSession, JobFailure, JobRun
from classbeat.jobs.kinds import Refused
from classbeat.models.jobs import GroupModel

RUNNING: Final = "running"
CYCLING: Final = "cycling"


@dataclass(frozen=True, slots=True)
class SportGate:
    running_enabled: bool = False  # CLASSBEAT_RUNNING_ENABLED


_gate = SportGate()


def configure(gate: SportGate | None) -> None:
    """Set the process's gate (None: running off)."""
    global _gate
    _gate = gate or SportGate()


def current() -> SportGate:
    return _gate


def sport_of(brief: Any) -> str:
    """The sport of a stored brief (a dict): ``running`` only when it says so, else cycling."""
    if isinstance(brief, dict) and brief.get("sport") == RUNNING:  # pyright: ignore[reportUnknownMemberType]
        return RUNNING
    return CYCLING


async def running_on(db: AsyncSession) -> bool:
    return await effective.running_enabled(db, current().running_enabled)


# The class's sport so far: fixed once its brief is stored or a revision of it exists; the
# stored brief's, else the newest design job's brief (a class whose songs were picked, before its
# design). The user's own rows only (RLS and owner_id).
_CLASS_SPORT = text(
    "SELECT (c.brief IS NOT NULL OR EXISTS (SELECT 1 FROM class_revisions r "
    "WHERE r.owner_id = c.owner_id AND r.class_id = c.id)) AS fixed, "
    "coalesce(c.brief, (SELECT j.input -> 'brief' FROM jobs j WHERE j.owner_id = c.owner_id "
    "AND j.class_id = c.id AND j.kind IN ('design_draft', 'design_with_songs') "
    "ORDER BY j.created_at DESC LIMIT 1)) AS brief "
    "FROM classes c WHERE c.owner_id = :owner_id AND c.id = :class_id"
)


async def admit_brief(
    db: AsyncSession, owner_id: UUID, brief: Brief, class_id: UUID | None
) -> Refused | None:
    """A design job's brief at creation: ``feature_off`` for running while the flag is off, then
    ``invalid_input`` when the class's sport is fixed and is another. None admits."""
    if brief.sport == RUNNING and not await running_on(db):
        return Refused("feature_off")
    if class_id is None:
        return None
    row = (await db.execute(_CLASS_SPORT, {"owner_id": owner_id, "class_id": class_id})).first()
    if row is not None and row.fixed and sport_of(row.brief) != brief.sport:
        return Refused("invalid_input")
    return None


_STORED_BRIEF = text("SELECT brief FROM classes WHERE owner_id = :owner_id AND id = :class_id")


async def admit_class(db: AsyncSession, owner_id: UUID, class_id: UUID) -> Refused | None:
    """A revise of a class: ``feature_off`` when the class is running and the flag is off."""
    brief = (
        await db.execute(_STORED_BRIEF, {"owner_id": owner_id, "class_id": class_id})
    ).scalar_one_or_none()
    if sport_of(brief) == RUNNING and not await running_on(db):
        return Refused("feature_off")
    return None


async def still_on(run: JobRun) -> None:
    """In the worker, before a running job calls anything: ``feature_off`` when the flag is off
    now (the job was queued while it was on)."""
    async with run.fenced() as fs:
        on = await running_on(fs.db)
    if not on:
        raise JobFailure("feature_off")


def require_evaluated(model: GroupModel) -> None:
    """In the worker, for a running job, after its ``design`` group's model is resolved and before
    anything is called: ``model_config`` unless that model's recorded result covers running."""
    if not model.running_covered:
        raise JobFailure("model_config")


_JOB_CLASS_BRIEF = text(
    "SELECT c.brief FROM classes c JOIN jobs j ON j.owner_id = c.owner_id AND j.class_id = c.id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id"
)


async def check_stored(fs: FencedSession, sport: str) -> None:
    """In a design's final fenced transaction, after ``revision.adopt_first``: the job's class
    has a stored brief of ``sport`` (a class without one is cycling, as every class before it).
    Another sport (a job of the other sport landed first) is ``invalid_input``, which rolls the
    whole store back."""
    brief = (
        await fs.db.execute(
            _JOB_CLASS_BRIEF, {"job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id}
        )
    ).scalar_one_or_none()
    if sport_of(brief) != sport:
        raise JobFailure("invalid_input")
