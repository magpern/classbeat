"""``ProvenanceOut.interpretation``: the stored reading of a request is answered as closed items
(ADR 0029 section 4, MA3), and a stored list that no longer checks never fails the read."""

from typing import Any

from classbeat.classes.provenance_out import ProvenanceOut

ITEM: dict[str, Any] = {
    "path": "length_min",
    "origin": "explicit",
    "value": 45,
    "quote": "45 minutes",
}
MISS: dict[str, Any] = {
    "path": "games.0",
    "origin": "not_as_asked",
    "reason_code": "plan_no_games",
    "value": {"type": "racetrack"},
}


def test_the_items_are_answered_in_order() -> None:
    out = ProvenanceOut.model_validate({"origin": "design_draft", "interpretation": [ITEM, MISS]})
    assert out.interpretation is not None
    assert [i.path for i in out.interpretation] == ["length_min", "games.0"]
    dumped = out.model_dump(mode="json", exclude_none=True)
    assert dumped["interpretation"][1]["reason_code"] == "plan_no_games"


def test_a_revision_without_a_reading_has_none() -> None:
    assert ProvenanceOut.model_validate({"origin": "design_draft"}).interpretation is None


def test_a_stored_list_that_does_not_check_is_left_out() -> None:
    bad = {"path": "length_min", "origin": "not_as_asked", "value": 1}  # no reason code
    assert ProvenanceOut.model_validate({"interpretation": [ITEM, bad]}).interpretation is None
    assert ProvenanceOut.model_validate({"interpretation": "nope"}).interpretation is None
