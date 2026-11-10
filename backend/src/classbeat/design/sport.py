"""A class's sport at the edges of the jobs (ADR 0018 sections 1, 2 and 5; M8 R1a).

The sport is a key of the validated brief (``Brief.sport``, absent = cycling). This module holds
the checks that are not the brief's own validation:

- **The gate** (ADR 0018 section 5 and its amendment of 2026-11-04, #509; the soundless gate's
  pattern, ``design.music_kind``). ``CLASSBEAT_RUNNING_ENABLED`` is the environment's kill switch
  (``SportGate.running_enabled``, set by the web app and the worker at start). Running is on only
  when all three hold: that switch, a stored ``running_enabled`` of exactly ``on`` (missing,
  ``null`` or invalid is off; ``flags.core.running_switch``), and a ``design`` route covering
  running for every plan's ``route_key`` (``flags.effective.running_enabled``, read on every
  request: ``running_on``, what ``GET /api/connections`` returns and the form reads). A new
  running design (``admit_brief``) and a revise of a running class (``admit_class``) go through
  ``admit_running`` in ``admit``, before the job row and before any credit is reserved, in this
  order: the switch (``feature_off``), the job's **own** plan's route (``running_not_evaluated``;
  before the next step, or this answer could never be given), every plan's route
  (``feature_off``). A queued running job ends ``feature_off`` in the worker when the switch is
  off by then (``still_on``), and ``model_config`` when its attempt's model does not cover running
  (``require_evaluated``), both before any model or MOWL call: the backstop for a route changed
  between ``admit`` and the run. Save and Update of a running class make no model call and read
  the switch only (``running_switch``). Cycling never reads any of it. In the web, ``fake``
  covers only when the web's own ``CLASSBEAT_MODEL_PROVIDER`` is ``fake`` outside prod
  (``music_kind.fake_covers``).
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
  ``require_evaluated`` ends it ``model_config`` before any MOWL or model call. The worker checks
  it on every attempt, so a route cleared after ``admit`` (no lock is shared with the route
  writes) costs at most that job, and nothing is sent.

Without a configured gate the switch is off, so running is refused everywhere.
"""

from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.brief import Brief
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.design.music_kind import fake_covers
from classbeat.flags import effective
from classbeat.jobs import FencedSession, JobFailure, JobRun
from classbeat.jobs.kinds import Refused
from classbeat.models.jobs import GroupModel

RUNNING: Final = "running"
CYCLING: Final = "cycling"


@dataclass(frozen=True, slots=True)
class SportGate:
    running_enabled: bool = False  # CLASSBEAT_RUNNING_ENABLED
    # The web's own ``fake`` provider outside prod (``music_kind.fake_covers``); never in prod.
    fake_covers: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "SportGate":
        return cls(running_enabled=settings.running_enabled, fake_covers=fake_covers(settings))


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


async def running_switch(db: AsyncSession) -> bool:
    """The environment's switch and a stored ``on`` (no coverage): Save, Update, the worker."""
    return await effective.running_switch(db, current().running_enabled)


async def running_on(db: AsyncSession) -> bool:
    """The effective ``running_enabled`` (switch and every plan covered), as the form sees it."""
    gate = current()
    return await effective.running_enabled(db, gate.running_enabled, fake_covers=gate.fake_covers)


async def _plan_covers(db: AsyncSession, ctx: UserContext) -> bool:
    if current().fake_covers:
        return True
    from classbeat.models.routes import covers  # as ``music_kind._plan_covers``
    from classbeat.plans.resolve import effective_plan

    plan = await effective_plan(db, ctx)
    return await covers(db, plan.route_key, "running")


async def admit_running(db: AsyncSession, ctx: UserContext) -> Refused | None:
    """A running design or revise at creation, in the order of the module: the switch
    (``feature_off``), the job's own plan's route (``running_not_evaluated``), every plan's route
    (``feature_off``). None admits. Reads only; reserves nothing."""
    if not await running_switch(db):
        return Refused("feature_off")
    if not await _plan_covers(db, ctx):
        return Refused("running_not_evaluated")
    if not await running_on(db):
        return Refused("feature_off")
    return None


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
    db: AsyncSession, ctx: UserContext, brief: Brief, class_id: UUID | None
) -> Refused | None:
    """A design job's brief at creation: the running gate (``admit_running``) for a running
    brief, then ``invalid_input`` when the class's sport is fixed and is another. None admits."""
    if brief.sport == RUNNING:
        refused = await admit_running(db, ctx)
        if refused is not None:
            return refused
    if class_id is None:
        return None
    params = {"owner_id": ctx.user_id, "class_id": class_id}
    row = (await db.execute(_CLASS_SPORT, params)).first()
    if row is not None and row.fixed and sport_of(row.brief) != brief.sport:
        return Refused("invalid_input")
    return None


_STORED_BRIEF = text("SELECT brief FROM classes WHERE owner_id = :owner_id AND id = :class_id")


async def admit_class(db: AsyncSession, ctx: UserContext, class_id: UUID) -> Refused | None:
    """A revise of a class: the running gate (``admit_running``) when the class is running."""
    brief = (
        await db.execute(_STORED_BRIEF, {"owner_id": ctx.user_id, "class_id": class_id})
    ).scalar_one_or_none()
    if sport_of(brief) == RUNNING:
        return await admit_running(db, ctx)
    return None


async def still_on(run: JobRun) -> None:
    """In the worker, before a running job calls anything: ``feature_off`` when the switch
    (environment and stored value) is off now. Coverage is the model's (``require_evaluated``)."""
    async with run.fenced() as fs:
        on = await running_switch(fs.db)
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
