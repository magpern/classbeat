"""The pace of polling spotdown (``poll_pace_calc``): doubles, honours ``retry_after`` and stays in
the budget. No database, no network."""

import pytest

from classbeat.spotdown import poll_pace_calc as pace


@pytest.mark.parametrize(
    ("rounds", "retry_after", "left", "delay"),
    [(0, None, 600, 5), (1, None, 600, 10), (3, None, 600, 30), (9, None, 600, 30),
     (0, 45.0, 600, 45), (2, None, 7.5, 7.5), (0, 600.0, 12, 12), (0, None, 0, 0)],
)  # fmt: skip
def test_the_pace_doubles_honours_retry_after_and_stays_in_the_budget(
    rounds: int, retry_after: float | None, left: float, delay: float
) -> None:
    assert pace.poll_delay(rounds, retry_after, left) == delay
