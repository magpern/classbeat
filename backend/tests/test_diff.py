"""The change summary's game wording (ADR 0029 section 3; issue #878). Pure: no database."""

import copy
import json
from typing import Any

from classbeat.design import diff


def iv(sec: int, zone: int, game: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"sec": sec, "zone": zone, "rpm": [80, 90], "standing": False}
    if game:
        out["game"] = game
    return out


def klass(*segments: tuple[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "name": "Team night",
        "description": "",
        "segments": [
            {"name": name, "kind": "main", "intervals": intervals} for name, intervals in segments
        ],
    }


BASE = klass(
    ("Warm up", [iv(120, 1)]),
    ("Main", [iv(120, 3), iv(30, 6, "racetrack"), iv(60, 2)]),
    ("Cool down", [iv(60, 1)]),
)


def with_main(*intervals: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(BASE)
    out["segments"][1]["intervals"] = list(intervals)
    return out


def test_no_game_change_is_an_empty_list() -> None:
    assert diff.summarize(BASE, copy.deepcopy(BASE))["game_changes"] == []


def test_a_changed_type_names_the_segment_counted_from_1_and_both_games() -> None:
    new = with_main(iv(120, 3), iv(30, 6, "overflow"), iv(60, 2))
    assert diff.summarize(BASE, new)["game_changes"] == [
        {"segment": 2, "before": "racetrack", "after": "overflow"}
    ]


def test_an_added_game_has_no_before() -> None:
    new = with_main(iv(120, 3), iv(30, 6, "racetrack"), iv(60, 2), iv(30, 6, "fast_lane"))
    assert diff.summarize(BASE, new)["game_changes"] == [
        {"segment": 2, "before": None, "after": "fast_lane"}
    ]


def test_a_removed_game_has_no_after() -> None:
    new = with_main(iv(120, 3), iv(30, 6), iv(60, 2))
    assert diff.summarize(BASE, new)["game_changes"] == [
        {"segment": 2, "before": "racetrack", "after": None}
    ]


def test_a_change_other_than_the_game_is_not_a_game_change() -> None:
    new = with_main(iv(120, 3), iv(45, 5, "racetrack"), iv(60, 2))
    change = diff.summarize(BASE, new)
    assert change["intervals"]["changed"] == 1
    assert change["game_changes"] == []


def test_a_legacy_game_in_the_old_draft_is_named_as_stored() -> None:
    old = with_main(iv(120, 3), iv(30, 6, "ghost_target"), iv(60, 2))
    assert diff.summarize(old, BASE)["game_changes"] == [
        {"segment": 2, "before": "ghost_target", "after": "racetrack"}
    ]


def test_an_added_segment_with_a_game_and_a_removed_one_are_listed() -> None:
    added = copy.deepcopy(BASE)
    added["segments"].insert(
        2, {"name": "Bonus", "kind": "main", "intervals": [iv(30, 6, "power_pull"), iv(60, 2)]}
    )
    assert diff.summarize(BASE, added)["game_changes"] == [
        {"segment": 3, "before": None, "after": "power_pull"}
    ]
    # The removed segment is counted in the old draft.
    assert diff.summarize(added, BASE)["game_changes"] == [
        {"segment": 3, "before": "power_pull", "after": None}
    ]


def test_the_list_is_in_segment_order_and_capped() -> None:
    many = klass(*[(f"Round {n}", [iv(30, 6, "racetrack"), iv(60, 2)]) for n in range(12)])
    plain = klass(*[(f"Round {n}", [iv(30, 6), iv(60, 2)]) for n in range(12)])
    found = diff.summarize(many, plain)["game_changes"]
    assert [g["segment"] for g in found] == list(range(1, diff.MAX_GAME_CHANGES + 1))


def test_the_summary_stays_under_the_stored_limit_even_when_capped() -> None:
    many = klass(*[(f"Round {n}", [iv(30, 6, "power_pull"), iv(60, 2)]) for n in range(12)])
    plain = klass(*[(f"Round {n}", [iv(30, 6), iv(60, 2)]) for n in range(12)])
    assert len(json.dumps(diff.summarize(many, plain)).encode()) <= diff.MAX_BYTES
