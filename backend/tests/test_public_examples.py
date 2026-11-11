"""The synthetic example classes of the public pages (ADR 0030, #894) obey the product's own rules.

``frontend/src/publicSite/examples.json`` is read by the frontend (the pages, the chart, the table)
and by this test, so a class shown on a public page is one the designer could have produced: the
library accepts it and the soft-rule engine raises no warning on it. No database or network.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from mowl import workout

from classbeat.agent.brief import Brief
from classbeat.design.rules.engine import evaluate
from classbeat.design.sport_rules import select_profile_for

FIXTURE = Path(__file__).resolve().parents[2] / "frontend/src/publicSite/examples.json"
DOCUMENT: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
EXAMPLES: list[dict[str, Any]] = DOCUMENT["examples"]
IDS = [e["slug"] for e in EXAMPLES]


def spec_of(example: dict[str, Any]) -> dict[str, Any]:
    """The library spec of a fixture. A running interval carries no cue in the library's shape."""
    segments = json.loads(json.dumps(example["segments"]))
    if example["sport"] == "running":
        for segment in segments:
            for interval in segment["intervals"]:
                interval.pop("cue", None)
    return {"name": example["slug"], "sport": example["sport"], "segments": segments}


def test_the_fixture_is_version_one_with_the_four_examples() -> None:
    assert DOCUMENT["version"] == 1
    assert IDS == ["hill-challenge", "90s-dance-intervals", "endurance-ride", "running-intervals"]
    assert [e["sport"] for e in EXAMPLES] == ["cycling", "cycling", "cycling", "running"]


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_the_library_accepts_the_class(example: dict[str, Any]) -> None:
    spec = spec_of(example)
    workout.validate(spec)
    assert workout.total_seconds(spec) == example["brief"]["length_min"] * 60


@pytest.mark.parametrize("example", EXAMPLES, ids=IDS)
def test_the_soft_rules_raise_no_warning(example: dict[str, Any]) -> None:
    brief = Brief(sport=example["sport"], **example["brief"])
    profile = select_profile_for(brief)
    assert profile is not None, "the class is judged against a real pattern profile"
    hits = evaluate(spec_of(example), brief, profile)
    assert [h.rule for h in hits if h.severity == "warn"] == []


def test_the_easy_ride_is_the_only_one_with_notes() -> None:
    """The other three sit inside their profile; an easy ride is lighter than the studio pattern,
    and its brief says so, which turns those notes into explained ones."""
    noted = {}
    for example in EXAMPLES:
        brief = Brief(sport=example["sport"], **example["brief"])
        hits = evaluate(spec_of(example), brief, select_profile_for(brief))
        noted[example["slug"]] = {h.rule: h.explained_by_brief for h in hits}
    assert {slug: hits for slug, hits in noted.items() if hits and slug != "endurance-ride"} == {}
    assert noted["endurance-ride"]
    assert all(noted["endurance-ride"].values())


def test_running_has_no_games_cadence_or_position() -> None:
    running = [e for e in EXAMPLES if e["sport"] == "running"]
    assert running
    for example in running:
        for segment in example["segments"]:
            for interval in segment["intervals"]:
                assert set(interval) <= {"sec", "zone", "gradient", "cue"}
                assert interval["gradient"] in (0, 1)
                assert 1 <= interval["zone"] <= 5


def test_no_example_has_a_game() -> None:
    """Games need a commercial MOWL licence (ADR 0029, G2); the examples claim none."""
    for example in EXAMPLES:
        for segment in example["segments"]:
            assert all("game" not in interval for interval in segment["intervals"])


def test_the_counts_a_brief_states_are_in_the_class() -> None:
    hill = next(e for e in EXAMPLES if e["slug"] == "hill-challenge")
    assert hill["brief"]["climbs"] == sum(s["name"].startswith("Climb ") for s in hill["segments"])
    dance = next(e for e in EXAMPLES if e["slug"] == "90s-dance-intervals")
    sprints = [
        i for s in dance["segments"] for i in s["intervals"] if i["zone"] >= 6 and i["sec"] <= 30
    ]
    assert dance["brief"]["sprints"] == len(sprints)
