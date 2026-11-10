"""Why the prompts ask for a margin over 10 s (issue #878): fitting to the music moves a boundary
by seconds, so a game the library accepts as written can fall under 10 s once fitted. The check
runs the library's rules again after fitting (``classbeat.agent.design.check``) and refuses it.
The 10 s rules themselves are tested in ``test_mowl_game_rules.py``. Fake music map: no network.
"""

import json
from pathlib import Path

import pytest

from classbeat.agent.design import check
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.evals.cases import Case
from gamescripts import MARGIN_GAME_SEC, margin_draft
from suitescripts import music_map

CASE_FILE = Path(__file__).parents[1] / "src/classbeat/evals/suite/games_ftp/games_margin_30.json"
CASE = Case.model_validate(json.loads(CASE_FILE.read_text(encoding="utf-8")))


def games_of(spec: dict) -> list[int]:  # type: ignore[type-arg]
    return [i["sec"] for s in spec["segments"] for i in s["intervals"] if i.get("game")]


def test_a_margin_over_10_seconds_survives_fitting() -> None:
    draft = WorkoutDraft.model_validate(margin_draft(CASE, MARGIN_GAME_SEC))
    result = check(draft, music_map(CASE))
    assert games_of(result.spec)
    assert min(games_of(result.spec)) >= 10


def test_a_game_just_over_10_seconds_can_fall_under_it_once_fitted() -> None:
    draft = WorkoutDraft.model_validate(margin_draft(CASE, 12))
    with pytest.raises(ToolError) as caught:
        check(draft, music_map(CASE))
    assert caught.value.code == "invalid_draft"
    assert "after fitting to the music" in caught.value.messages[0]
