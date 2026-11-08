"""The effective Save and Update caps of a plan (issue #806, ADR 0003 amendment 2026-11-05).

Pure: no database, no settings. ``CLASSBEAT_SAVES_PER_HOUR`` and ``CLASSBEAT_SAVES_PER_DAY`` (the
bounds) are what every user gets; a plan may carry a value of its own (``plans.saves_per_hour``,
``plans.saves_per_day``, NULL for none) that can only tighten them: the effective cap is the
smaller of the two, and a plan without a value uses the bound.
"""

from typing import NamedTuple


class SaveCaps(NamedTuple):
    per_hour: int
    per_day: int


def effective_cap(plan_value: int | None, bound: int) -> int:
    """The plan's value under the bound; the bound when the plan has none."""
    return bound if plan_value is None else min(plan_value, bound)


def effective_save_caps(
    plan_per_hour: int | None, plan_per_day: int | None, bound_per_hour: int, bound_per_day: int
) -> SaveCaps:
    return SaveCaps(
        effective_cap(plan_per_hour, bound_per_hour), effective_cap(plan_per_day, bound_per_day)
    )
