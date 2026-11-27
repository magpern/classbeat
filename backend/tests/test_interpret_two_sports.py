"""Both sports in one request, and the form's sport against the request (#1544; ADR 0018 Amendment
6), through the jobs API and the worker side, against Postgres as ``classbeat_app``, the fake MOWL
and ``FakeProvider``. Never the network, a real account or a model.

Covered: a reading the interpreter marks ``two_sports`` is stored ended (its Not as asked item, no
question, the refusal ``two_sports``), answered as such by ``GET .../interpretation``, and the
hand-off, a retry included, creates no design job and takes no class credit; running needs no
switch (no ``app_settings`` row, no gate configured); the form's sport against the request's is
the closed ``which_sport`` question with the form's sport assumed, and the instructor's answer is
the re-read's ``sport_override``; agreeing sports ask nothing."""

from typing import Any
from uuid import UUID

import pytest

from interprettools import REQUEST, InterpretEnv

pytestmark = pytest.mark.usefixtures("jenv")

BOTH: dict[str, Any] = {
    "length_min": {"value": 20, "origin": "explicit", "quote": "20"},
    "two_sports": True,
    # A model's question on a reading that ends: never stored. Its topic is one the request does not
    # state (intensity has no reading), so the policy would keep it on any other reading (#1558).
    "questions": [
        {
            "topic": "intensity",
            "text": "How hard?",
            "options": ["Easy", "Hard"],
            "assumed_index": 0,
        }
    ],
}
SPIN: dict[str, Any] = {
    "sport": {"value": "cycling", "origin": "inferred"},
    "length_min": {"value": 45, "origin": "explicit"},
}


def no_running_row(ienv: InterpretEnv) -> None:
    """No admin row and no gate: running needs no switch since #1544 (migration 0082)."""
    keys = ienv.sql("SELECT key FROM app_settings").scalars().all()
    assert "running_enabled" not in keys


async def test_both_sports_end_the_reading_with_no_design_job_and_no_credit(
    ienv: InterpretEnv,
) -> None:
    no_running_row(ienv)
    class_id, job_id = await ienv.interpreted(BOTH, spotify=False, sport_override="running")
    stored = ienv.interpretation(class_id)
    assert stored["refusal"] == {"reason": "two_sports", "counts": {}}
    assert stored["items"][0] == {
        "path": "sport",
        "origin": "not_as_asked",
        "reason_code": "two_sports",
        "value": ["cycling", "running"],
        "quote": None,
    }
    assert [i for i in stored["items"] if i["path"] == "sport"] == [stored["items"][0]]
    assert "questions" not in stored
    assert stored.get("music_decision") is None
    answer = await ienv.renv.browser.get(f"/api/classes/{class_id}/interpretation")
    assert answer.status_code == 200, answer.text
    assert answer.json()["refusal"]["reason"] == "two_sports"
    before = ienv.counts()
    for body in (
        {"interpret_job_id": str(job_id)},
        {"interpret_job_id": str(job_id), "retry": True},
    ):
        refused = await ienv.handoff(class_id, **body)
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["reason"] == "two_sports"
    assert ienv.design_jobs(class_id) == []
    assert ienv.counts() == before  # no job and no credit, the retry included
    assert ienv.credits() == 0


async def test_a_run_needs_no_switch_and_is_designed_as_running(ienv: InterpretEnv) -> None:
    no_running_row(ienv)
    reading = {"sport": {"value": "running", "origin": "explicit", "quote": "run"}}
    class_id, _ = await ienv.interpreted(reading, request="A 45 min run", spotify=False)
    stored = ienv.interpretation(class_id)
    assert stored["brief"]["sport"] == "running"
    assert stored.get("refusal") is None
    assert stored["music"]["source"] == "mowl_spotify_playlist"  # a MOWL playlist (ADR 0018)


async def test_the_forms_sport_against_the_request_asks_which_sport(ienv: InterpretEnv) -> None:
    # With Spotify and the user's MOWL playlist chosen: a cycling answer then has its music.
    class_id, job_id = await ienv.interpreted(SPIN, sport_override="running")
    stored = ienv.interpretation(class_id)
    assert stored["brief"]["sport"] == "running"  # the manual choice wins until it is answered
    assert stored["questions"][0] == {
        "topic": "sport",
        "code": "which_sport",
        "options": ["cycling", "running"],
        "assumed_index": 1,
    }
    # The instructor answers "cycling": the re-read's closed sport_override, in the app's words.
    body = {
        "interpret_job_id": str(job_id),
        "answers": [{"question_index": 0, "option_index": 0}],
    }
    made = await ienv.renv.browser.post(f"/api/classes/{class_id}/reread", body)
    assert made.status_code == 201, made.text
    given = ienv.stored_input(UUID(made.json()["job_id"]))
    assert given["sport_override"] == "cycling"
    assert given["request"] == f"{REQUEST} Is the class cycling or running? Answer: Cycling"


async def test_agreeing_sports_ask_nothing_about_the_sport(ienv: InterpretEnv) -> None:
    reading = {"sport": {"value": "running", "origin": "explicit", "quote": "run"}}
    class_id, _ = await ienv.interpreted(reading, spotify=False, sport_override="running")
    stored = ienv.interpretation(class_id)
    assert all(q["topic"] != "sport" for q in stored.get("questions", []))


async def test_no_sport_sent_the_reading_decides_and_nothing_is_recorded_as_chosen(
    ienv: InterpretEnv,
) -> None:
    # "a class we run on Tuesdays" (#1558): the toggle may flip on the verb, but the page sends no
    # sport_override for a flip by the words. The model names no sport it is sure of, so the class
    # is cycling, the sport is an inference (never `explicit`), `which_sport` is not asked (it needs
    # a form sport to disagree with) and the model's own sport question is kept.
    reading = {
        "sport": {"value": "cycling", "origin": "inferred"},
        "questions": [
            {
                "topic": "sport",
                "text": "Is this a ride or a run?",
                "options": ["A ride", "A run"],
                "assumed_index": 0,
            }
        ],
    }
    class_id, _ = await ienv.interpreted(
        reading, request="A 45 min class we run on Tuesdays", spotify=False
    )
    stored = ienv.interpretation(class_id)
    assert stored["brief"].get("sport", "cycling") == "cycling"
    assert [i["origin"] for i in stored["items"] if i["path"] == "sport"] == ["inferred"]
    asked = stored["questions"]
    assert [(q["topic"], q.get("code")) for q in asked] == [("sport", None)]
    assert asked[0]["text"] == "Is this a ride or a run?"
