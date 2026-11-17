"""``classbeat.jobs.discard_policy`` (ADR 0038): the allowlist of the empty first creation, on its
own facts. No database."""

from dataclasses import replace

import pytest

from classbeat.jobs.discard_policy import Facts, candidate, may_discard

READING = Facts(
    kind="interpret_request",
    state="failed",
    credit_state="none",
    has_version=False,
    has_reading=False,
    revisions=0,
    other_jobs=0,
    readings=0,
    kept=False,
)
DESIGN = replace(
    READING,
    kind="design_draft",
    credit_state="reserved",
    has_reading=True,
    other_jobs=1,
    readings=1,
)


@pytest.mark.parametrize(
    "facts",
    [
        READING,
        replace(READING, state="cancelled"),
        DESIGN,
        replace(DESIGN, state="cancelled"),
        replace(DESIGN, kind="design_with_songs"),
        replace(DESIGN, credit_state="released"),
        replace(DESIGN, has_reading=False),  # the hand-off's design whose reading row went
    ],
)
def test_the_empty_shell_of_a_first_creation_goes(facts: Facts) -> None:
    assert may_discard(facts)


@pytest.mark.parametrize(
    "facts",
    [
        replace(READING, state="completed"),
        replace(READING, state="expired"),
        replace(READING, state="queued"),
        replace(READING, state="running"),
        replace(READING, kind="revise_draft"),
        replace(READING, kind="save_workout"),
        replace(READING, kind="studio_design"),
        replace(READING, has_version=True),
        replace(READING, revisions=1),
        replace(READING, kept=True),
        replace(READING, has_reading=True),  # a reading stored, whatever its job says
        replace(READING, other_jobs=1),
        replace(READING, credit_state="committed"),
        replace(DESIGN, readings=0),  # a design that is not the reading's own hand-off
        replace(DESIGN, other_jobs=2),  # a second attempt, or any other job
        replace(DESIGN, other_jobs=0, readings=0),  # a design with no reading (the class form)
        replace(DESIGN, revisions=1),  # a song list waiting or approved
        replace(DESIGN, has_version=True),
        replace(DESIGN, kept=True),
        replace(DESIGN, state="expired"),
    ],
)
def test_anything_else_keeps_the_class(facts: Facts) -> None:
    assert not may_discard(facts)


def test_only_a_failed_or_stopped_creation_is_a_candidate() -> None:
    assert candidate("interpret_request", "failed")
    assert candidate("design_with_songs", "cancelled")
    assert not candidate("interpret_request", "completed")
    assert not candidate("revise_draft", "failed")
