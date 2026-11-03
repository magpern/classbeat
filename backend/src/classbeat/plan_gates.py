"""A plan's class length cap and feature gates, as a model job carries them (issue #496).

``PlanGates`` is fixed when the job is created (``classbeat.quota.route.with_plan``: the effective
plan in the creating transaction, under the environment's ``CLASSBEAT_CLASS_MINUTES_MAX``) and
stored with the input as a server field, like ``route_key``: a browser may never send it, every
attempt and the approved phase of a job that waited read the same value, and the worker, which
has no plan of its own, enforces exactly what the user was admitted under. An input stored
before it existed has ``None``: no gate (it was admitted before there were any).

``draft_problems`` is the worker's check of a fitted draft, called by the design and revise
stages' ``preview_draft`` and ``submit_draft`` (``classbeat.agent``): a draft with a game or an
FTP test the plan does not include, or longer than the plan's cap (with the evals' 5 %
tolerance, since the draft is fitted to the music), comes back to the model as a tool error to
fix, so a revision that asks for games or a longer class can never be stored. Races are not a
field of a draft (they are short efforts like any other), so their gate is the brief's ``races``
count only, checked at admit.

This module is a leaf (pydantic only): the jobs' input models and the agent both import it.
"""

from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

# The evals' duration tolerance (``classbeat.evals.checks.DURATION_TOLERANCE``): a draft is
# fitted to the music, so a 30-minute class may come out a little longer.
LENGTH_TOLERANCE: Final = 0.05
FIELD: Final = "plan_gates"


class PlanGates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_class_minutes: int = Field(ge=10, le=180)
    games: bool
    races: bool
    ftp_tests: bool


def max_draft_seconds(gates: PlanGates) -> int:
    """The longest fitted draft the plan accepts, in whole seconds."""
    return int(gates.max_class_minutes * 60 * (1 + LENGTH_TOLERANCE))


def draft_problems(
    gates: PlanGates | None, spec: Mapping[str, Any], duration_sec: int
) -> list[str]:
    """What the plan does not allow in a fitted spec, as sentences for the model; empty when
    nothing (or when the job carries no gates)."""
    if gates is None:
        return []
    intervals = [i for s in spec.get("segments", ()) for i in s.get("intervals", ())]
    problems: list[str] = []
    if not gates.games and any(i.get("game") is not None for i in intervals):
        problems.append("This instructor's plan does not include games: remove every game.")
    if not gates.ftp_tests and any(i.get("ftp_test") is not None for i in intervals):
        problems.append("This instructor's plan does not include FTP tests: remove the FTP test.")
    if duration_sec > max_draft_seconds(gates):
        problems.append(
            f"This instructor's plan allows classes of at most {gates.max_class_minutes} "
            "minutes: make the class shorter."
        )
    return problems
