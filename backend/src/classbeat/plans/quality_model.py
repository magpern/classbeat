"""The quality level of a plan (ADR 0031 section 8): the word users see, never a model name.

Pure: the mapping from plan id to word. The model behind a word is the plan's model route
(ADR 0012, ``classbeat.admin.model_routes``), which an admin sets; a plan with no route runs the
bootstrap model. ``agent_runs.route_key`` equals the plan id, so the quality follows the plan.
"""

from typing import Final, Literal

Quality = Literal["standard", "better", "best"]

_BY_PLAN: Final[dict[str, Quality]] = {"free": "standard", "instructor": "better", "pro": "best"}
LOWEST: Final[Quality] = "standard"


def quality_for(plan_id: str) -> Quality:
    """``free`` is standard, ``instructor`` better and ``pro`` best. A plan added later has no
    word until this table names it, and falls back to the lowest rather than promising more."""
    return _BY_PLAN.get(plan_id, LOWEST)
