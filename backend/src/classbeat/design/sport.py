"""A class's sport at the edges of the jobs (ADR 0018 sections 1, 2 and 5, and its Amendment 6;
M8 R1a, #1544).

The sport is a key of the validated brief (``Brief.sport``, absent = cycling). This module holds
the checks that are not the brief's own validation:

- **No switch** (ADR 0018 Amendment 6, #1544; owner, 2026-11-26: running is always available).
  Nothing hides or refuses a running design, revise, refit, duplicate, Save or Update on a switch
  or a flag: the environment's running switch, the stored ``running_enabled`` and the effective
  flag of Amendment 5 are gone. The checks below and Save's approval are unchanged.
- **Model coverage** (ADR 0012 section 5 and its amendment of 2026-11-03). A running job uses the
  running prompts on the ``design`` group's model, resolved as for any job (the route, or the
  bootstrap choice without one; a route with a newer failure is still ``model_config``).
  ``serve_model`` runs in the worker after the model is resolved and before anything is called: a
  model with no recorded running coverage (``GroupModel.running_covered``) serves the running
  class with the stale-route warning, reason ``running_not_evaluated``, once per process and route
  key, instead of refusing (the pattern of ``music_kind.serve_model``, #1543). The running schema
  and ``workout.validate_running`` still check every draft.
- **Immutable once a revision exists** (section 1). A design job on an existing class whose
  first revision exists (or whose brief is stored) is refused ``invalid_input`` when its brief's
  sport is not the class's (``admit_brief``); the final store checks it again in its fenced
  transaction (``check_stored``), so two queued jobs of different sports cannot both land.
- **Running v1 designs on an existing MOWL playlist only** (section 2, Amendment 2): no Spotify
  song picking and no Swedebeat mix. The input models refuse those combinations
  (``DesignDraftInput``, ``DesignWithSongsInput``), and the song kinds refuse a running brief
  again in the worker.
"""

import logging
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.agent.brief import Brief
from classbeat.context import UserContext
from classbeat.jobs import FencedSession, JobFailure
from classbeat.jobs.kinds import Refused
from classbeat.models.jobs import GroupModel

log = logging.getLogger(__name__)

RUNNING: Final = "running"
CYCLING: Final = "cycling"
# The stale-route warning's reason for a model with no recorded running coverage (ADR 0012
# section 5, #1544): logged, never refused.
UNCOVERED: Final = "running_not_evaluated"


def sport_of(brief: Any) -> str:
    """The sport of a stored brief (a dict): ``running`` only when it says so, else cycling."""
    if isinstance(brief, dict) and brief.get("sport") == RUNNING:  # pyright: ignore[reportUnknownMemberType]
        return RUNNING
    return CYCLING


# The class's sport so far: fixed once its brief is stored or a revision of it exists; the
# stored brief's, else the newest design job's brief (a class whose songs were picked, before its
# design). The user's own rows only (RLS and owner_id).
_CLASS_SPORT = text(
    "SELECT (c.brief IS NOT NULL OR EXISTS (SELECT 1 FROM class_revisions r "
    "WHERE r.owner_id = c.owner_id AND r.class_id = c.id)) AS fixed, "
    "coalesce(c.brief, (SELECT j.input -> 'brief' FROM jobs j WHERE j.owner_id = c.owner_id "
    "AND j.class_id = c.id AND j.kind IN ('design_draft', 'design_with_songs', 'studio_songs', "
    "'studio_design') "
    "ORDER BY j.created_at DESC LIMIT 1)) AS brief "
    "FROM classes c WHERE c.owner_id = :owner_id AND c.id = :class_id"
)


async def admit_brief(
    db: AsyncSession, ctx: UserContext, brief: Brief, class_id: UUID | None
) -> Refused | None:
    """A design job's brief at creation: ``invalid_input`` when the class's sport is fixed and is
    another (no switch, #1544). None admits. Reads the user's own rows only."""
    if class_id is None:
        return None
    params = {"owner_id": ctx.user_id, "class_id": class_id}
    row = (await db.execute(_CLASS_SPORT, params)).first()
    if row is not None and row.fixed and sport_of(row.brief) != brief.sport:
        return Refused("invalid_input")
    return None


_warned: set[str | None] = set()


def serve_model(model: GroupModel) -> None:
    """In the worker, for a running job, after its ``design`` group's model is resolved and before
    anything is called (#1544): always served; on a model without recorded running coverage with
    the stale-route warning (ADR 0012 section 5), once per process and route key (``None``: the
    bootstrap choice). Logs codes only."""
    if model.running_covered:
        return
    key = model.agent.route_key
    if key in _warned:
        return
    _warned.add(key)
    log.warning(
        "model route stale",
        extra={"route_key": key, "stage_group": "design", "reason": UNCOVERED},
    )


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
