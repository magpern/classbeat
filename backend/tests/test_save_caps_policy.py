"""The effective Save and Update caps of a plan (#806, ``classbeat.quota.save_caps_policy``)."""

import pytest

from classbeat.quota.save_caps_policy import SaveCaps, effective_cap, effective_save_caps


@pytest.mark.parametrize(
    ("plan", "bound", "expected"),
    [
        (None, 30, 30),  # unset: the setting
        (10, 30, 10),  # tighter: the plan
        (30, 30, 30),
        (500, 30, 30),  # looser: never; the setting
    ],
)
def test_a_plan_value_tightens_and_never_loosens(
    plan: int | None, bound: int, expected: int
) -> None:
    assert effective_cap(plan, bound) == expected


def test_hour_and_day_are_independent() -> None:
    assert effective_save_caps(5, None, 30, 100) == SaveCaps(5, 100)
    assert effective_save_caps(None, 40, 30, 100) == SaveCaps(30, 40)
    assert effective_save_caps(99, 999, 30, 100) == SaveCaps(30, 100)
