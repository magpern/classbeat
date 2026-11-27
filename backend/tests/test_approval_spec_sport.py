"""``approval.spec_of`` and the spec's own sport (#1558, from the review of #1557).

``draft_model(sport)`` already fixes the spec's sport (``RunningDraft.to_spec`` always says
``running``, ``WorkoutDraft.to_spec`` never does), so no stored content can make
``workout.sport_of(spec) != sport`` true today: the check is defence in depth against the
sport-to-schema mapping drifting. The test below reaches it by simulating that drift (the
running class handed the cycling schema), and shows that the real mapping refuses each sport's
content for the other. Pure: no database, no network."""

from typing import Any

import pytest

from classbeat.agent.schemas import WorkoutDraft
from classbeat.saving import approval
from savetools import DRAFT, RUNNING_DRAFT


def test_each_sports_content_is_a_spec_of_that_sport_only() -> None:
    cycling = approval.spec_of(DRAFT, "cycling")
    running = approval.spec_of(RUNNING_DRAFT, "running")
    assert cycling is not None
    assert running is not None
    assert running["sport"] == "running"
    assert "sport" not in cycling  # a cycling spec has no "sport"
    assert approval.spec_of(DRAFT, "running") is None  # the strict running schema refuses it
    assert approval.spec_of(RUNNING_DRAFT, "cycling") is None


def test_a_spec_of_another_sport_than_the_class_is_refused_if_the_schema_mapping_drifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line this reaches: ``workout.sport_of(spec) != sport`` in ``spec_of``."""

    def drifted(sport: Any) -> type[WorkoutDraft]:
        return WorkoutDraft  # every sport, the running class too, gets the cycling schema

    monkeypatch.setattr(approval, "draft_model", drifted)
    assert approval.spec_of(DRAFT, "cycling") is not None  # the schema and the sport agree
    assert approval.spec_of(DRAFT, "running") is None  # a cycling spec is not a running class's
