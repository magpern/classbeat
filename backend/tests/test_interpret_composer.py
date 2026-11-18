"""The composer's part of ``interpret_request`` (ADR 0038 sections 2 and 3, #1185): the
``composer_flow`` switch, ``music_sources`` at admit, pinned songs and follow-up questions in the
reading and how they are stored. Pure: no database, no network, no model.
"""

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from classbeat.agent.interpret import InterpretContext, InterpretResult, interpret_stage
from classbeat.agent.interpret_input import first_message
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.agent.interpreted_composer import MIN_LETTERS, from_request
from classbeat.agent.stage import ToolAnswer
from classbeat.design.interpret_composer_policy import composer_reading
from classbeat.design.interpret_kind import InterpretRequestInput
from classbeat.design.interpret_policy import Offer, resolve
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.flags import core
from classbeat.plan_gates import PlanGates

PLAYLIST = "0123456789abcdefABCDEF"
OTHER = "ZZZZZZZZZZabcdefABCDEF"
MOWL = str(uuid4())
SPOTIFY = str(uuid4())
REQUEST = (
    "A 45 minute class with three climbs. Warm up to Dancing Queen by ABBA and make "
    "Africa by Toto the last climb."
)


def source(playlist_id: str = PLAYLIST) -> dict[str, str]:
    return {"kind": "spotify_playlist", "id": playlist_id}


def parsed(**more: Any) -> InterpretRequestInput:
    values: dict[str, Any] = {"request": REQUEST, "mowl_connection_id": MOWL}
    return InterpretRequestInput.model_validate(values | more)


# --- the switch (ADR 0038 section 2: the running_enabled pattern) ------------------------------


@pytest.mark.parametrize(
    ("environment_on", "stored", "expected"),
    [
        (False, "on", False),  # never past the environment's switch
        (True, None, False),  # nothing stored, or the cleared JSON null
        (True, "off", False),
        (True, "yes", False),  # invalid: off
        (True, True, False),
        (True, "on", True),  # only a stored "on" under the switch
    ],
)
def test_only_a_stored_on_under_the_environment_switches_the_composer_on(
    environment_on: bool, stored: Any, expected: bool
) -> None:
    assert core.composer_switch(environment_on, stored) is expected


def test_the_composer_flag_is_a_key_and_stores_off_or_on_only() -> None:
    assert "composer_flow" in core.KEYS
    for value in ("off", "on"):
        assert core.validate_for_write("composer_flow", value, trial_days_max=30) == value
    for bad in ("yes", True, None, 1):
        with pytest.raises(core.FlagRefused) as refused:
            core.validate_for_write("composer_flow", bad, trial_days_max=30)
        assert refused.value.reason == "invalid_value"


# --- music_sources at admit (shape and count only) ---------------------------------------------


@pytest.mark.parametrize(
    ("more", "ok"),
    [
        ({"music_sources": [source()]}, True),
        ({"music_sources": [source(), source(OTHER)]}, True),
        ({"music_sources": [source(PLAYLIST[:-1] + "!")]}, False),  # not base-62
        ({"music_sources": [source(PLAYLIST + "A")]}, False),  # 23 characters
        ({"music_sources": [{"kind": "swedebeat_mix", "id": PLAYLIST}]}, False),  # Spotify only
        ({"music_sources": [{"kind": "spotify_playlist", "id": PLAYLIST, "x": 1}]}, False),
        ({"music_sources": [source(f"{n:022d}") for n in range(5)]}, True),
        ({"music_sources": [source(f"{n:022d}") for n in range(6)]}, False),  # more than 5
        ({"music_sources": []}, False),
        ({"music_sources": [source(), source()]}, False),  # the same playlist twice
        ({"music_sources": [source()], "music_override": {"source": "none"}}, False),
        ({"music_sources": [source()], "music_override": {"source": "pick"}}, True),
    ],
)
def test_music_sources_are_checked_for_shape_and_count(more: dict[str, Any], ok: bool) -> None:
    values = {"spotify_connection_id": SPOTIFY} | more
    if ok:
        got = parsed(**values).music_sources
        assert got is not None
        assert len(got) == len(more["music_sources"])
    else:
        with pytest.raises(ValidationError):
            parsed(**values)


def test_attached_music_needs_the_spotify_connection() -> None:
    with pytest.raises(ValueError, match="Spotify connection"):
        parsed(music_sources=[source()])
    assert parsed().music_sources is None  # the old start screen's request is unchanged


def test_attached_music_is_never_an_override() -> None:
    """Favour these, never use only these: the music override and the order stay as they are."""
    got = parsed(spotify_connection_id=SPOTIFY, music_sources=[source()])
    assert got.music_override is None
    offer = Offer(spotify=True, pick_ready=True)
    resolved = resolve(InterpretedRequest(), REQUEST, offer)
    assert resolved.music == {"source": "pick"}


# --- what the interpreter is told --------------------------------------------------------------


def test_the_message_says_how_many_playlists_are_attached_and_nothing_else() -> None:
    kwargs: dict[str, Any] = {
        "sports": ("cycling",),
        "max_class_minutes": 60,
        "games_allowed": True,
        "music": (),
    }
    plain = first_message(REQUEST, **kwargs)
    assert plain == first_message(REQUEST, attached=0, **kwargs)  # byte-identical
    assert "attached_playlists" not in json.loads(plain)
    attached = json.loads(first_message(REQUEST, attached=2, **kwargs))
    assert attached["attached_playlists"] == 2
    assert PLAYLIST not in json.dumps(attached)


# --- the reading: pinned songs --------------------------------------------------------------


def submitted(request: str, reading: dict[str, Any]) -> InterpretedRequest:
    """What the ``submit_brief`` handler hands back for ``reading`` (sanitised)."""
    stage = interpret_stage(InterpretContext(request))
    answer = stage.tools[0].handler(InterpretedRequest.model_validate(reading))
    assert isinstance(answer, ToolAnswer)
    assert isinstance(answer.output, InterpretResult)
    return answer.output.request


def pin(song: str, artist: str | None = None, at: str | None = None, **more: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"song": song, "origin": "explicit", **more}
    if artist is not None:
        out["artist"] = artist
    if at is not None:
        out["at"] = at
    return out


@pytest.mark.parametrize(
    ("pins", "expected"),
    [
        (
            [pin("Dancing Queen", "ABBA", "Warm up"), pin("Africa", "Toto", "the last climb")],
            [
                {"title": "Dancing Queen", "artist": "ABBA", "at": "Warm up"},
                {"title": "Africa", "artist": "Toto", "at": "the last climb"},
            ],
        ),
        # the request's own spelling, case aside
        (
            [pin("dancing queen", "abba", "warm up")],
            [{"title": "Dancing Queen", "artist": "ABBA", "at": "Warm up"}],
        ),
        # a title the request does not hold is dropped; an artist or a place it does not hold
        # is left out
        ([pin("Waterloo", "ABBA")], []),
        ([pin("Africa", "Weezer", "climb 3")], [{"title": "Africa"}]),
        # the same song once
        (
            [pin("Africa", "Toto"), pin("AFRICA", "toto", "the last climb")],
            [{"title": "Africa", "artist": "Toto"}],
        ),
        # a quote is kept only verbatim
        ([pin("Africa", quote="make Africa")], [{"title": "Africa", "quote": "make Africa"}]),
        ([pin("Africa", quote="play Africa")], [{"title": "Africa"}]),
    ],
)
def test_pinned_songs_are_kept_in_the_requests_words_with_their_place(
    pins: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> None:
    read = submitted(REQUEST, {"pinned_songs": pins})
    resolved = resolve(read, REQUEST, Offer(spotify=True, pick_ready=True))
    stored, _ = composer_reading(read, resolved.brief)
    assert [p.model_dump(exclude_none=True) for p in stored] == expected


def test_a_pinned_song_is_explicit_only_and_bounded() -> None:
    with pytest.raises(ValidationError):
        InterpretedRequest.model_validate(
            {"pinned_songs": [pin("Africa") | {"origin": "inferred"}]}
        )
    with pytest.raises(ValidationError):
        InterpretedRequest.model_validate({"pinned_songs": [pin("Africa", at="x" * 41)]})
    with pytest.raises(ValidationError):
        InterpretedRequest.model_validate({"pinned_songs": [pin("Africa")] * 11})


# --- the reading: questions -----------------------------------------------------------------


def stated(value: Any, origin: str = "explicit") -> dict[str, Any]:
    return {"value": value, "origin": origin}


GATES = PlanGates(max_class_minutes=30, games=True, races=False, ftp_tests=False)


@pytest.mark.parametrize(
    ("reading", "gates", "expected"),
    [
        # nothing stated: the length and the effort are asked, the brief's defaults assumed
        (
            {"questions": [{"path": "length_min"}, {"path": "intensity"}]},
            None,
            [("length_min", "how_long", 45), ("intensity", "how_hard", "moderate")],
        ),
        # the plan's longest class is what the brief takes, so it is what is assumed
        ({"questions": [{"path": "length_min"}]}, GATES, [("length_min", "how_long", 30)]),
        # an explicit value is never asked about
        (
            {
                "length_min": stated(45),
                "questions": [{"path": "length_min"}, {"path": "intensity"}],
            },
            None,
            [("intensity", "how_hard", "moderate")],
        ),
        ({"intensity": stated("hard"), "questions": [{"path": "intensity"}]}, None, []),
        # an inferred value may be asked about: the brief's value is the assumed one
        (
            {"length_min": stated(30, "inferred"), "questions": [{"path": "length_min"}]},
            None,
            [("length_min", "how_long", 30)],
        ),
        # one question per path
        (
            {"questions": [{"path": "intensity"}, {"path": "intensity"}]},
            None,
            [("intensity", "how_hard", "moderate")],
        ),
        ({}, None, []),
    ],
)
def test_questions_are_asked_only_of_open_shape_fields_with_the_briefs_value(
    reading: dict[str, Any], gates: PlanGates | None, expected: list[tuple[str, str, Any]]
) -> None:
    read = submitted("A climbing class", reading)
    resolved = resolve(read, "A climbing class", Offer(gates=gates, spotify=True, pick_ready=True))
    _, questions = composer_reading(read, resolved.brief)
    assert [(q.path, q.question, q.assumed) for q in questions] == expected
    for q in questions:  # the brief uses the assumed value; nothing waits for an answer
        assert getattr(resolved.brief, q.path) == q.assumed
        assert q.path not in resolved.brief.explicit


def test_a_question_names_a_closed_path_only_and_at_most_two() -> None:
    for bad in ({"path": "climbs"}, {"path": "length_min", "question": "How long?"}):
        with pytest.raises(ValidationError):
            InterpretedRequest.model_validate({"questions": [bad]})
    with pytest.raises(ValidationError):
        InterpretedRequest.model_validate({"questions": [{"path": "length_min"}] * 3})


# --- how it is stored --------------------------------------------------------------------------


def test_a_reading_without_pins_or_questions_is_stored_exactly_as_before() -> None:
    base = {"job_id": uuid4(), "brief": {"length_min": 45}, "music": None, "items": []}
    dumped = json.loads(StoredInterpretation.model_validate(base).dumped())
    assert set(dumped) == {"v", "job_id", "brief", "music", "items", "refusal"}
    read = submitted(
        REQUEST,
        {"pinned_songs": [pin("Africa", "Toto")], "questions": [{"path": "intensity"}]},
    )
    pins, questions = composer_reading(read, resolve(read, REQUEST, Offer()).brief)
    full = StoredInterpretation.model_validate(
        base | {"pinned_songs": pins, "questions": questions}
    )
    stored = json.loads(full.dumped())
    assert stored["pinned_songs"] == [{"title": "Africa", "artist": "Toto"}]
    assert stored["questions"] == [
        {"path": "intensity", "question": "how_hard", "assumed": "moderate"}
    ]
    assert StoredInterpretation.model_validate(stored) == full  # it reads back


def test_a_change_of_songs_never_stores_the_composers_part() -> None:
    """``songs.revise_reading.storable``: a list's provenance keeps the keys it had before."""
    from classbeat.songs.revise_reading import storable

    read = submitted(
        REQUEST, {"pinned_songs": [pin("Africa")], "questions": [{"path": "intensity"}]}
    )
    assert storable(read) == {"games": []}


@pytest.mark.parametrize(
    ("words", "text", "expected"),
    [
        ("africa", "Make Africa the last climb", "Africa"),
        ("a", "Make Africa the last climb", None),  # a lone letter is never a pin
        ("  ", "Make Africa the last climb", None),
        ("fri", "Make Africa the last climb", None),  # inside a word
        ("Afric", "Make Africa the last climb", None),  # ends inside a word
        ("U2", "One by U2 for the warm-up", "U2"),
        ("warm-up", "One by U2 for the warm-up.", "warm-up"),
        ("the last climb", "Africa, the last climb!", "the last climb"),
        # a fold that changes the length elsewhere in the request changes nothing
        ("Africa", "Straße climb, then Africa", "Africa"),
        ("straße", "Ride to Straße by Kraftwerk", "Straße"),
        ("Strasse", "Ride to Straße by Kraftwerk", None),  # the request's own spelling only
        ("Mamma Mia", "låten MAMMA MIA ska vara backe 3", "MAMMA MIA"),
    ],
)
def test_pin_words_are_whole_words_of_the_request(
    words: str, text: str, expected: str | None
) -> None:
    assert from_request(words, text, 100) == expected


@pytest.mark.parametrize(
    ("words", "text", "expected"),
    [
        ("Africa", "Africa", "Africa"),  # the whole request: no neighbour on either side
        ("Africa", "Africa then a climb", "Africa"),  # at the very start
        ("climb", "Africa then a climb", "climb"),  # at the very end
        ("U2", "Two by U22 tonight", None),  # a digit after it: inside a word
        ("U2", "Two by 2U2 tonight", None),  # a digit before it: inside a word
        ("U2", "Two by U2, then more", "U2"),  # punctuation is a boundary
        ("a.", "Make a. climb", None),  # one letter and a full stop is still one letter
        ("é", "Make é climb", None),  # one non-ASCII letter is a lone letter too
        ("(live)", "Africa (live) at the end", "(live)"),  # no word edge to respect on a bracket
        ("Mia", "Mamma Mia!", "Mia"),
        ("Mam", "Mamma Mia!", None),
    ],
)
def test_pin_words_respect_word_edges_and_the_letter_floor(
    words: str, text: str, expected: str | None
) -> None:
    assert from_request(words, text, 100) == expected


def test_pin_words_need_exactly_the_minimum_letters_or_digits() -> None:
    below = "x" * (MIN_LETTERS - 1)
    at = "x" * MIN_LETTERS
    assert from_request(below, f"play {below} now", 100) is None
    assert from_request(at, f"play {at} now", 100) == at
    # punctuation never counts towards the floor
    assert from_request("-" + below + "-", f"play -{below}- now", 100) is None


def test_pin_words_longer_than_the_limit_are_dropped_not_cut() -> None:
    assert from_request("Africa", "Make Africa the last climb", 6) == "Africa"
    assert from_request("Africa", "Make Africa the last climb", 5) is None


def test_the_composer_flow_is_off_by_default() -> None:
    from classbeat.config import Settings
    from classbeat.design import composer

    settings = Settings(environment="test")
    assert settings.composer_flow is False
    assert composer.ComposerGate.from_settings(settings).enabled is False
    assert composer.ComposerGate().enabled is False
    assert core.composer_switch(settings.composer_flow, "on") is False
