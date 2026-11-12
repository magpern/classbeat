"""The admin Settings view's trial: what a trial started now would be (ADR 0011 section 7).

Moved out of ``classbeat.admin.settings`` (split on touch, #976). Pure: no database.
"""

from typing import Any

from classbeat.config import Settings
from classbeat.flags import core
from classbeat.plans.entitle import TRIAL_MAX_PLAN


def effective_trial(
    trial: core.TrialSetting | None, settings: Settings, ranks: dict[str, int]
) -> dict[str, Any] | None:
    """What a trial started now would be, as ``classbeat.plans.trial`` clamps it: the days to
    ``CLASSBEAT_TRIAL_DAYS_MAX``, the plan to at most ``instructor``; ``None`` when off."""
    if trial is None:
        return None
    days = min(trial.days, settings.trial_days_max)
    if days <= 0 or trial.plan not in ranks or TRIAL_MAX_PLAN not in ranks:
        return None
    plan = TRIAL_MAX_PLAN if ranks[trial.plan] > ranks[TRIAL_MAX_PLAN] else trial.plan
    return {"plan": plan, "days": days}
