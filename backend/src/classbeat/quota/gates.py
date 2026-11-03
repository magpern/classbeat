"""A plan's class length cap and feature gates at job creation (issue #496).

``gates_for`` turns the effective plan into the ``PlanGates`` a model job carries (the cap under
``CLASSBEAT_CLASS_MINUTES_MAX``); ``check_brief`` refuses, with ``QuotaRefused`` (409 ``quota``),
a brief the plan does not allow: ``length`` when ``length_min`` is over the cap, ``feature``
``races`` for races on a plan without them, ``feature`` ``ftp_tests`` for an FTP test (the brief's
``ftp_test`` or the ``ftp_test`` class type) on a plan without them. Games are not a field of the
brief (the model adds them when the instructor's words ask for them), so their gate is enforced on
every draft by the worker (``classbeat.plan_gates.draft_problems``).

``brief_of`` is the brief a job designs for: the input's own (``design_draft``,
``design_with_songs``) or, for a revise kind, the class's stored brief, read in the creating
transaction with ``owner_id`` (``None`` when the class is not the user's: the kind's own
``admit`` refuses it). The brief is read as JSON, not as ``classbeat.agent.Brief``: this module is
imported by the jobs API and must not import the agent.

A stored draft that becomes current, or is saved, **without a model run** (#496, second PR): "Make
current" (``classbeat.classes.service.pick``), Save and Update (``classbeat.saving``) refuse a
revision the user's **current** plan does not include with ``check_stored_draft``: the effective
plan read in the caller's transaction (never the plan the revision was designed under), then
``check_content`` on the draft's intervals (``game``, ``ftp_test``) and its length (the sum of the
interval ``sec``, under the same cap plus tolerance the worker applies,
``classbeat.plan_gates.max_draft_seconds``): 409 ``quota`` ``feature`` (``games`` or
``ftp_tests``) or ``length`` (``used`` in whole minutes, rounded up).
"""

import math
from collections.abc import Iterator, Mapping
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.context import UserContext, require_context
from classbeat.jobs.credits import QuotaRefused
from classbeat.plan_gates import PlanGates, max_draft_seconds
from classbeat.plans.resolve import Plan, effective_plan
from classbeat.quota import bounds as quota_bounds
from classbeat.quota.bounds import Bounds, effective

_STORED_BRIEF = text("SELECT brief FROM classes WHERE owner_id = :o AND id = :c")


def gates_for(plan: Plan, bounds: Bounds) -> PlanGates:
    return PlanGates(
        max_class_minutes=effective(plan, bounds).max_class_minutes,
        games=plan.games,
        races=plan.races,
        ftp_tests=plan.ftp_tests,
    )


async def brief_of(
    db: AsyncSession, ctx: UserContext, parsed: BaseModel
) -> Mapping[str, Any] | None:
    """The brief the job designs for, as JSON; ``None`` when there is none to check."""
    ctx = require_context(ctx)
    brief = getattr(parsed, "brief", None)
    if isinstance(brief, BaseModel):
        return brief.model_dump(mode="json")
    class_id = getattr(parsed, "class_id", None)
    if not isinstance(class_id, UUID):
        return None
    stored = (
        await db.execute(_STORED_BRIEF, {"o": ctx.user_id, "c": class_id})
    ).scalar_one_or_none()
    return stored if isinstance(stored, dict) else None  # pyright: ignore[reportUnknownVariableType]


def check_brief(gates: PlanGates, brief: Mapping[str, Any] | None, plan_id: str) -> None:
    """Raise ``QuotaRefused`` ``length`` or ``feature`` for a brief the plan does not allow."""
    if brief is None:
        return
    length = brief.get("length_min")
    if (
        isinstance(length, int)
        and not isinstance(length, bool)
        and length > gates.max_class_minutes
    ):
        raise QuotaRefused("length", limit=gates.max_class_minutes, used=length, plan=plan_id)
    races = brief.get("races")
    if not gates.races and isinstance(races, int) and races > 0:
        raise QuotaRefused("feature", feature="races", plan=plan_id)
    ftp = brief.get("ftp_test") is not None or brief.get("class_type") == "ftp_test"
    if not gates.ftp_tests and ftp:
        raise QuotaRefused("feature", feature="ftp_tests", plan=plan_id)


async def current_gates(db: AsyncSession, ctx: UserContext) -> tuple[Plan, PlanGates]:
    """The user's effective plan now and its gates under the environment's bounds."""
    plan = await effective_plan(db, ctx)
    return plan, gates_for(plan, quota_bounds.current())


def _intervals(content: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    segments = content.get("segments")
    for segment in segments if isinstance(segments, list) else ():
        intervals = segment.get("intervals") if isinstance(segment, Mapping) else None
        for interval in intervals if isinstance(intervals, list) else ():
            if isinstance(interval, Mapping):
                yield interval  # pyright: ignore[reportUnknownArgumentType]


def content_seconds(content: Mapping[str, Any]) -> int:
    """A stored draft's (or a library spec's) length: the sum of its intervals' whole ``sec``."""
    return sum(
        sec
        for i in _intervals(content)
        if isinstance(sec := i.get("sec"), int) and not isinstance(sec, bool) and sec > 0
    )


def check_content(gates: PlanGates, content: Mapping[str, Any], plan_id: str) -> None:
    """Raise ``QuotaRefused`` ``feature`` or ``length`` for a stored draft the plan does not
    include: a game, an FTP test, or longer than the cap (with the worker's tolerance)."""
    intervals = list(_intervals(content))
    if not gates.games and any(i.get("game") is not None for i in intervals):
        raise QuotaRefused("feature", feature="games", plan=plan_id)
    if not gates.ftp_tests and any(i.get("ftp_test") is not None for i in intervals):
        raise QuotaRefused("feature", feature="ftp_tests", plan=plan_id)
    seconds = content_seconds(content)
    if seconds > max_draft_seconds(gates):
        raise QuotaRefused(
            "length", limit=gates.max_class_minutes, used=math.ceil(seconds / 60), plan=plan_id
        )


async def check_stored_draft(
    db: AsyncSession, ctx: UserContext, content: Mapping[str, Any]
) -> None:
    """``check_content`` against the user's **current** plan (Make current, Save, Update)."""
    plan, gates = await current_gates(db, ctx)
    check_content(gates, content, plan.id)
