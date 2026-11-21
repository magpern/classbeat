"""Every unreachable reason ends a job in one category (ADR 0040 amendment item 4); no database."""

from typing import get_args

import pytest

from classbeat.jobs import CATEGORIES, RETRYABLE
from classbeat.spotdown.failure_policy import UnreachableReason
from classbeat.spotdown.outage_policy import log_message, unreachable_category

UNAVAILABLE = ("not_configured", "source_off", "source_disabled", "blocked", "connection",
               "unauthorized", "server_error")  # fmt: skip
FAILED = ("too_large", "rejected", "bad_answer")
INTERNAL = ("conflict",)


def test_the_three_groups_cover_every_reason_exactly_once() -> None:
    grouped = [*UNAVAILABLE, *FAILED, *INTERNAL]
    assert sorted(grouped) == sorted(get_args(UnreachableReason))
    assert len(set(grouped)) == len(grouped)


@pytest.mark.parametrize("reason", get_args(UnreachableReason))
def test_each_reason_maps_to_the_category_of_the_adr(reason: UnreachableReason) -> None:
    expected = (
        "analysis_unavailable"
        if reason in UNAVAILABLE
        else "analysis_failed"
        if reason in FAILED
        else "internal"
    )
    category = unreachable_category(reason)
    assert category == expected
    assert category in CATEGORIES
    assert (category in RETRYABLE) == (reason in UNAVAILABLE)


def test_a_value_outside_the_closed_set_is_internal_and_never_logged() -> None:
    odd: UnreachableReason = "https://spotdown.example/x"  # pyright: ignore[reportAssignmentType]
    assert unreachable_category(odd) == "internal"
    assert log_message(odd) == "spotdown unreachable: category=internal reason=unknown"


@pytest.mark.parametrize("reason", ["blocked", "unauthorized", "connection"])
def test_the_log_line_names_the_reason_next_to_the_category(reason: UnreachableReason) -> None:
    assert log_message(reason) == (
        f"spotdown unreachable: category=analysis_unavailable reason={reason}"
    )
