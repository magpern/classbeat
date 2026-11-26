"""The reading's questions for a song pick with no hint (#1536, roadmap Amendment 37;
``reading_question_policy``, ``music_hint_policy``, ``interpret_reread_policy`` in ``design``):
a hint-less pick always has a music question with no assumed answer, the model's or the closed
``which_music``; a pick chosen in the form is no hint; "No music" is never the assumed answer.
Pure: no database, no network, no model."""

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from classbeat.agent.interpret import InterpretContext, InterpretResult, interpret_stage
from classbeat.agent.interpret_input import MusicName
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.interpreted_composer import MAX_QUESTIONS
from classbeat.agent.stage import ToolAnswer
from classbeat.design.interpret_reread_policy import RereadIn, chose_no_music, sentences
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.design.music_hint_policy import has_hint, hintless
from classbeat.design.reading_question_policy import (
    WHICH_MUSIC,
    WHICH_MUSIC_OPTIONS,
    Question,
    chosen_topics,
    questions,
)

PICK = {"source": "pick"}
SOURCES = [{"kind": "spotify_playlist", "id": "0123456789abcdefABCDEF"}]


def submitted(reading: dict[str, Any], *, named: bool = False) -> InterpretedRequest:
    """The sanitised reading; ``named``: the instructor has one playlist the model may name."""
    names = (MusicName("playlist", "Tuesday ride"),) if named else ()
    stage = interpret_stage(InterpretContext("A climbing class", music=names))
    answer = stage.tools[0].handler(InterpretedRequest.model_validate(reading))
    assert isinstance(answer, ToolAnswer)
    assert isinstance(answer.output, InterpretResult)
    return answer.output.request


def ask(topic: str, assumed: int | None = 0, **more: Any) -> dict[str, Any]:
    """A well-formed question: a music one marks its "No music" option (Amendment 37 decision 6);
    ``more`` overrides, ``no_music_index=None`` leaves the marker out."""
    question = {"topic": topic, "text": f"{topic}?", "options": ["Pop", "Rock", "No music"]}
    marked = {"no_music_index": 2} if topic == "music" else {}
    given = question | marked | ({} if assumed is None else {"assumed_index": assumed}) | more
    return {k: v for k, v in given.items() if v is not None}


def stored(
    reading: dict[str, Any], skip: Any = (), *, hint_less: bool, named: bool = False
) -> list[Question]:
    return questions(submitted(reading, named=named), skip, hintless=hint_less)


# --- the hint ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, False),
        ({"override": "pick"}, False),  # "Pick songs for me" says nothing about the music
        ({"override": "mowl_spotify_playlist"}, True),
        ({"override": "mowl_swedebeat_mix"}, True),
        ({"override": "none"}, True),
        ({"sources": SOURCES}, True),
        ({"pinned": 1}, True),
    ],
)
def test_a_hint_is_anything_the_instructor_gave_but_a_pick(
    kwargs: dict[str, Any], expected: bool
) -> None:
    given: dict[str, Any] = {"pinned": 0, "override": None, "sources": None} | kwargs
    assert has_hint([], None, **given) is expected
    assert hintless(PICK, [], None, **given) is not expected
    assert hintless({"source": "none"}, [], None, **given) is False  # only a pick needs one
    assert hintless(None, [], None, **given) is False


# --- a hint-less pick always asks about the music ---------------------------------------------


@pytest.mark.parametrize(
    ("reading", "skip"),
    [
        ({"questions": [ask("music")]}, ()),  # the model assumed an answer: it is removed
        ({"questions": [ask("music")]}, {"music"}),  # a pick chosen in an older form's skip
        # a name the model read that was not taken (the policy is told the pick has no hint)
        ({"music": {"value": 0, "origin": "explicit"}, "questions": [ask("music")]}, ()),
    ],
    ids=["assumed", "skipped", "stated"],
)
def test_the_models_music_question_is_kept_with_no_assumed_answer(
    reading: dict[str, Any], skip: Any
) -> None:
    named = "music" in reading  # the name is in range, so the reading states it
    assert (submitted(reading, named=named).music is not None) is named
    [music] = stored(reading, skip, hint_less=True, named=named)
    assert (music.topic, music.code, music.text, music.assumed_index) == (
        "music",
        None,
        "music?",
        None,
    )
    assert len(stored(reading, skip, hint_less=False, named=named)) == (0 if skip or named else 1)


@pytest.mark.parametrize(
    "reading",
    [
        {},  # the model asked nothing
        {"questions": [ask("length")]},  # it asked about something else
        {"music": {"value": 0, "origin": "explicit"}},  # a name that matched nothing: dropped
        {"questions": [ask("other", text=f"Q{i}?") for i in range(MAX_QUESTIONS)]},  # the cap
    ],
    ids=["none asked", "other topic", "unmatched name", "at the cap"],
)
def test_with_no_music_question_the_closed_one_is_added_first(reading: dict[str, Any]) -> None:
    got = stored(reading, hint_less=True)
    assert got[0] == WHICH_MUSIC
    assert [q.topic for q in got].count("music") == 1
    assert len(got) <= MAX_QUESTIONS
    assert WHICH_MUSIC not in stored(reading, hint_less=False)


def test_the_closed_question_is_not_added_twice() -> None:
    got = stored({"questions": [ask("length"), ask("music", assumed=None)]}, hint_less=True)
    assert [(q.topic, q.code) for q in got] == [("length", None), ("music", None)]


def test_a_pick_is_no_topic_the_form_settled() -> None:
    """``interpret_kind`` passes ``music`` only for a chosen source or attached playlists."""
    assert chosen_topics(music=False, sport=False) == frozenset()
    assert chosen_topics(music=True, sport=True) == {"music", "sport"}


# --- the closed question ----------------------------------------------------------------------


def test_the_closed_question_has_no_model_text_and_closed_options() -> None:
    assert WHICH_MUSIC.text is None
    assert tuple(WHICH_MUSIC.options) == WHICH_MUSIC_OPTIONS
    assert (WHICH_MUSIC.assumed_index, WHICH_MUSIC.no_music_index) == (None, 1)
    dumped = WHICH_MUSIC.model_dump(mode="json")
    assert Question.model_validate(dumped) == WHICH_MUSIC


@pytest.mark.parametrize(
    "bad",
    [
        {"topic": "music", "options": ["a", "b"]},  # neither text nor code
        {"topic": "music", "code": "which_music", "text": "Words?", "options": ["a", "b"]},
        {"topic": "length", "code": "which_music", "options": list(WHICH_MUSIC_OPTIONS)},
        {"topic": "music", "code": "which_music", "options": ["Pop", "Rock"], "no_music_index": 1},
        {"topic": "music", "code": "which_music", "options": list(WHICH_MUSIC_OPTIONS)},
        {
            "topic": "music",
            "code": "which_music",
            "options": list(WHICH_MUSIC_OPTIONS),
            "assumed_index": 0,
            "no_music_index": 1,
        },
        {"topic": "music", "code": "other_code", "options": list(WHICH_MUSIC_OPTIONS)},
    ],
)
def test_a_question_is_the_models_or_exactly_the_closed_one(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(bad)


def test_the_closed_question_is_stored_and_read_back() -> None:
    base = {"job_id": uuid4(), "brief": {"length_min": 45}, "music": PICK, "items": []}
    row = StoredInterpretation.model_validate(base | {"questions": [WHICH_MUSIC]})
    again = StoredInterpretation.model_validate_json(row.dumped())
    assert again.questions == [WHICH_MUSIC]


def test_answers_to_the_closed_question_are_the_apps_words() -> None:
    reading = uuid4()

    def body(answer: dict[str, Any], **more: Any) -> RereadIn:
        return RereadIn.model_validate_json(
            json.dumps(
                {
                    "interpret_job_id": str(reading),
                    "answers": [{"question_index": 0} | answer],
                    **more,
                }
            )
        )

    asked = [WHICH_MUSIC]
    typed = body({"text": "80s rock"})
    assert sentences(asked, typed) == "What music should the class have? Answer: 80s rock"
    assert not chose_no_music(asked, typed)
    # "Use one of my playlists" names none by itself (it would loop back to the same question):
    # it is an answer only with the playlists the picker sent (#1538)
    assert sentences(asked, body({"option_index": 0})) is None
    playlists = body(
        {"option_index": 0}, music_sources=[{"kind": "spotify_playlist", "id": "a" * 22}]
    )
    assert sentences(asked, playlists) == (
        "What music should the class have? Answer: Use one of my playlists"
    )
    assert not chose_no_music(asked, playlists)
    none = body({"option_index": 1})
    assert chose_no_music(asked, none)
    assert sentences(asked, body({"option_index": 2})) is None  # no such option


# --- "No music" is never assumed --------------------------------------------------------------


def test_the_no_music_marker_is_dropped_when_it_is_the_assumed_answer() -> None:
    # with a hint, the marker on the assumed answer is dropped, so the question offers no
    # "No music" and is dropped with it; nothing takes its place
    assert stored({"questions": [ask("music", assumed=2, no_music_index=2)]}, hint_less=False) == []
    [other] = stored({"questions": [ask("music", assumed=0, no_music_index=2)]}, hint_less=False)
    assert (other.assumed_index, other.no_music_index) == (0, 2)
    # a hint-less pick assumes nothing, so its "No music" option keeps its marker
    [required] = stored({"questions": [ask("music", assumed=2, no_music_index=2)]}, hint_less=True)
    assert (required.assumed_index, required.no_music_index) == (None, 2)


# --- "No music" is always offered -------------------------------------------------------------

STYLES = ["Pop", "Rock", "80s", "90s", "Hits"]


@pytest.mark.parametrize(
    "music",
    [
        ask("music", assumed=None, no_music_index=None, options=["Pop", "Rock"]),  # no marker
        ask("music", assumed=None, no_music_index=None, options=STYLES),  # 5 styles, no marker
        ask("music", assumed=None, no_music_index=2, options=["Pop", "Rock"]),  # == len
        ask("music", assumed=None, no_music_index=7, options=STYLES),  # beyond the options
    ],
    ids=["no marker", "five unmarked", "marker at len", "marker beyond"],
)
def test_a_music_question_offering_no_no_music_is_replaced_by_the_closed_one(
    music: dict[str, Any],
) -> None:
    """The Architect, 2026-11-26: kept only with a marker in range, never mended."""
    got = stored({"questions": [ask("length"), music]}, hint_less=True)
    assert [(q.topic, q.code) for q in got] == [("music", "which_music"), ("length", None)]
    assert got[0] == WHICH_MUSIC
    assert stored({"questions": [ask("length"), music]}, hint_less=False) == got[1:]


def test_a_marker_on_the_assumed_answer_is_kept_only_when_nothing_is_assumed() -> None:
    music = ask("music", assumed=2, no_music_index=2)
    [required] = stored({"questions": [music]}, hint_less=True)
    assert (required.code, required.assumed_index, required.no_music_index) == (None, None, 2)
    assert stored({"questions": [music]}, hint_less=False) == []


def test_a_negative_marker_never_reaches_the_policy() -> None:
    with pytest.raises(ValidationError):
        submitted({"questions": [ask("music", no_music_index=-1)]})


def test_a_later_well_formed_music_question_is_kept_after_an_unmarked_one() -> None:
    unmarked = ask("music", assumed=None, no_music_index=None, text="Which style?")
    got = stored({"questions": [unmarked, ask("music", assumed=None)]}, hint_less=True)
    assert [(q.text, q.no_music_index) for q in got] == [("music?", 2)]


@pytest.mark.parametrize(
    "marker",
    [None, -1, 2, 5, 0, True],
    ids=["none", "negative", "len", "beyond", "assumed", "bool"],
)
def test_every_stored_music_question_offers_no_music(marker: Any) -> None:
    """``Question`` refuses one, and a stored list drops one when read (the row stays readable)."""
    entry = {"topic": "music", "text": "Music?", "options": ["Pop", "Rock"], "assumed_index": 0}
    entry |= {} if marker is None else {"no_music_index": marker}
    with pytest.raises(ValidationError):
        Question.model_validate(entry)
    base = {"job_id": uuid4(), "brief": {"length_min": 45}, "music": PICK, "items": []}
    row = StoredInterpretation.model_validate(base | {"questions": [entry, ask("length")]})
    assert [q.topic for q in row.questions] == ["length"]
    good = entry | {"no_music_index": 1}
    assert Question.model_validate(good).no_music_index == 1
