"""Both sports in one request, and the form's sport against the request's (#1544; ADR 0018
Amendment 6): ``design.two_sports_policy``, ``reading_question_policy.which_sport`` and the
re-read's closed sport answer (``interpret_reread_policy``), and the hand-off's ``two_sports``
(``interpretation_design.design_input``). Pure: no database, no network, no model. The job and the
hand-off through Postgres are ``test_interpret_two_sports``."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.classes.interpretation_design import design_input
from classbeat.design import interpret_reread_policy as reread
from classbeat.design import reading_question_policy as rq
from classbeat.design import two_sports_policy as two
from classbeat.design.interpretation_row import StoredInterpretation

PLAYLIST = {"source": "mowl_spotify_playlist", "spotify_playlist_id": "OwnPlaylist00000000001"}
JOB_INPUT = {"request": "A ride, then a run", "mowl_connection_id": str(uuid.uuid4())}


def read(**fields: Any) -> InterpretedRequest:
    return InterpretedRequest.model_validate(fields)


def sport(value: str) -> dict[str, Any]:
    return {"value": value, "origin": "explicit"}


# --- the flag and the ended reading ------------------------------------------------------------


def test_only_a_true_flag_asks_for_both() -> None:
    assert two.asked_both(read(two_sports=True))
    assert not two.asked_both(read(two_sports=False))
    assert not two.asked_both(read())
    assert not two.asked_both(read(sport=sport("running")))


def test_the_ended_items_say_two_sports_first_and_drop_the_other_sport_item() -> None:
    kept = InterpretationItem(path="length_min", origin="explicit", value=20)
    held = InterpretationItem(path="sport", origin="default", value="cycling")
    ended = two.ended_items([held, kept])
    assert [i.model_dump(exclude_none=True) for i in ended] == [
        {
            "path": "sport",
            "origin": "not_as_asked",
            "reason_code": "two_sports",
            "value": ["cycling", "running"],
        },
        kept.model_dump(exclude_none=True),
    ]
    assert two.ended(ended)
    assert not two.ended([held, kept])
    other = InterpretationItem(
        path="sport", origin="not_as_asked", reason_code="sport_not_offered", value="running"
    )
    assert not two.ended([other])


def stored(items: list[InterpretationItem], music: dict[str, str] | None) -> StoredInterpretation:
    brief = {"length_min": 20, "intensity": "moderate"}
    return StoredInterpretation(job_id=uuid.uuid4(), brief=brief, music=music, items=items)


@pytest.mark.parametrize("music", [PLAYLIST, None])
@pytest.mark.parametrize("composer", [True, False])
def test_the_handoff_answers_two_sports_before_anything_else(
    music: dict[str, str] | None, composer: bool
) -> None:
    """Whatever the music (none at all would be ``needs_spotify``) and the flow: no design kind."""
    row = stored(two.ended_items([]), music)
    got = design_input(row, JOB_INPUT, uuid.uuid4(), song_picking=True, composer=composer)
    assert got == "two_sports"
    assert design_input(stored([], PLAYLIST), JOB_INPUT, uuid.uuid4(), song_picking=True) != (
        "two_sports"
    )


# --- which sport, when the form and the words disagree ----------------------------------------


@pytest.mark.parametrize(
    ("override", "named", "reread_", "assumed"),
    [
        ("running", "cycling", False, 1),  # the form's sport is assumed: a manual choice wins
        ("cycling", "running", False, 0),
        ("running", "running", False, None),  # they agree: nothing to ask
        ("cycling", "cycling", False, None),
        ("running", None, False, None),  # the words name no sport
        (None, "running", False, None),  # no sport chosen in the form: the words decide
        ("running", "cycling", True, None),  # a re-read carries the answer as its override
    ],
)
def test_which_sport_is_asked_only_when_the_form_and_the_words_disagree(
    override: str | None, named: str | None, reread_: bool, assumed: int | None
) -> None:
    got = rq.which_sport(
        read(**({"sport": sport(named)} if named else {})), override, reread=reread_
    )
    if assumed is None:
        assert got is None
        return
    assert got is not None
    assert (got.topic, got.code, got.text, got.options, got.assumed_index) == (
        "sport",
        "which_sport",
        None,
        ["cycling", "running"],
        assumed,
    )
    assert got.no_music_index is None


def test_which_sport_goes_first_and_drops_the_models_sport_question() -> None:
    model = {
        "topic": "sport",
        "text": "Bike or run?",
        "options": ["Bike", "Run"],
        "assumed_index": 0,
    }
    length = {"topic": "length", "text": "How long?", "options": ["30", "45"], "assumed_index": 1}
    reading = read(sport=sport("cycling"), questions=[model, length])
    asked = rq.which_sport(reading, "running", reread=False)
    out = rq.questions(reading, sport=asked)
    assert [(q.topic, q.code) for q in out] == [("sport", "which_sport"), ("length", None)]


@pytest.mark.parametrize(
    "bad",
    [
        {"options": ["running", "cycling"]},
        {"assumed_index": None},
        {"assumed_index": 2},
        {"topic": "music"},
        {"text": "Which sport?"},
        {"no_music_index": 1},
    ],
)
def test_a_closed_sport_question_is_exactly_which_sport(bad: dict[str, Any]) -> None:
    good: dict[str, Any] = {
        "topic": "sport",
        "code": "which_sport",
        "options": ["cycling", "running"],
        "assumed_index": 0,
    }
    rq.Question.model_validate(good)
    with pytest.raises(ValidationError):
        rq.Question.model_validate({**good, **bad})


# --- the answer, as the re-read's closed sport_override ----------------------------------------


def test_the_answer_to_which_sport_is_the_rereads_sport_override_in_the_apps_words() -> None:
    asked = rq.which_sport(read(sport=sport("cycling")), "running", reread=False)
    assert asked is not None
    length = rq.Question(topic="length", text="How long?", options=["30", "45"], assumed_index=0)
    questions = [asked, length]
    interpret_job_id = uuid.uuid4()
    body = reread.RereadIn.model_validate(
        {
            "interpret_job_id": interpret_job_id,
            "answers": [{"question_index": 0, "option_index": 0}],
        }
    )
    assert reread.chosen_sport(questions, body) == "cycling"
    assert reread.sentences(questions, body) == "Is the class cycling or running? Answer: Cycling"
    kept = {"request": "A spin class", "sport_override": "running"}
    fields = reread.browser_fields(kept, body, no_music=False, sport="cycling")
    assert fields["sport_override"] == "cycling"
    # Typed words never set it, and another question's option is no sport.
    typed = reread.RereadIn.model_validate(
        {"interpret_job_id": interpret_job_id, "answers": [{"question_index": 0, "text": "run"}]}
    )
    assert reread.chosen_sport(questions, typed) is None
    other = reread.RereadIn.model_validate(
        {
            "interpret_job_id": interpret_job_id,
            "answers": [{"question_index": 1, "option_index": 1}],
        }
    )
    assert reread.chosen_sport(questions, other) is None
    assert reread.browser_fields(kept, other, no_music=False)["sport_override"] == "running"
