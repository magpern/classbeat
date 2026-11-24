"""The composer's music decision (``classbeat.design.music_decision``; ADR 0038 amendment of
2026-11-24, decision 5, #1495): what the reading sets, what an answer may say, what the song
search takes from it. Pure: no database, no network, no model."""

from typing import Any

import pytest
from pydantic import ValidationError

from classbeat.agent.brief import Brief
from classbeat.agent.interpreted import InterpretedRequest
from classbeat.design import music_decision
from classbeat.design.interpret_music_policy import MusicOption
from classbeat.design.interpret_policy import Offer, resolve
from classbeat.design.interpretation_row import StoredInterpretation
from classbeat.design.music_decision import (
    DEFAULT,
    MAX_BRIEF_TEXT,
    NOTE_LABEL,
    MusicAnswer,
    MusicDecision,
    answered,
    brief_with_note,
    decision_of,
    fill_sources,
    initial,
)
from classbeat.design.music_sources import MusicSource

A, B = "0123456789abcdefABCDEF", "ABCDEF0123456789abcdef"
UNDECIDED = MusicDecision(state="undecided")


def source(playlist: str) -> dict[str, str]:
    return {"kind": "spotify_playlist", "id": playlist}


def read(**fields: Any) -> InterpretedRequest:
    return InterpretedRequest.model_validate(fields)


# --- the reading's decision -----------------------------------------------------------------------


def test_a_request_that_leaves_the_music_open_is_undecided() -> None:
    assert initial(read(), composer=True, attached=False, chose=False) == UNDECIDED
    empty_taste = read(music_taste={})  # a taste the interpreter returned with nothing in it
    assert initial(empty_taste, composer=True, attached=False, chose=False) == UNDECIDED


@pytest.mark.parametrize(
    ("reading", "attached", "chose"),
    [
        ({}, True, False),  # the request attached playlists
        ({}, False, True),  # it chose a source (a playlist, a mix or "No music")
        ({"music": {"value": 0, "origin": "explicit"}}, False, False),  # it named a playlist
        ({"music_taste": {"hits": {"value": True, "origin": "explicit"}}}, False, False),
        ({"music_taste": {"language": {"value": "sv", "origin": "inferred"}}}, False, False),
    ],
)
def test_a_request_that_says_what_the_music_is_is_decided_as_requested(
    reading: dict[str, Any], attached: bool, chose: bool
) -> None:
    got = initial(read(**reading), composer=True, attached=attached, chose=chose)
    assert got == DEFAULT == MusicDecision(state="decided", choice="request")


def test_only_a_composer_cycling_class_gets_a_decision() -> None:
    assert initial(read(), composer=False, attached=False, chose=False) is None


def test_a_row_stored_before_reads_as_decided_as_requested() -> None:
    assert decision_of(None) == DEFAULT
    row = StoredInterpretation.model_validate(
        {"job_id": "00000000-0000-0000-0000-000000000001", "brief": {}, "music": None, "items": []}
    )
    assert row.music_decision is None
    assert "music_decision" not in row.dumped()  # absent stays absent: rows are as before


# --- an answer --------------------------------------------------------------------------------


def test_each_answer_sets_its_decision() -> None:
    assert answered(MusicAnswer(choice="later")) == UNDECIDED
    assert answered(MusicAnswer(choice="pick")) == MusicDecision(state="decided", choice="pick")
    shared = answered(MusicAnswer.model_validate({"choice": "playlists", "playlists": [source(A)]}))
    assert (shared.state, shared.choice, [s.id for s in shared.sources]) == (
        "decided",
        "playlists",
        [A],
    )
    described = answered(MusicAnswer(choice="describe", note="Dark techno, no vocals"))
    assert (described.choice, described.note) == ("describe", "Dark techno, no vocals")


@pytest.mark.parametrize(
    "body",
    [
        {"choice": "playlists"},  # no playlists
        {"choice": "playlists", "playlists": []},
        {"choice": "playlists", "playlists": [source(c * 22) for c in "abcdef"]},  # six
        {"choice": "playlists", "playlists": [source(A), source(A)]},  # one twice
        {"choice": "playlists", "playlists": [source("not-a-spotify-id")]},
        {"choice": "playlists", "playlists": [{"kind": "swedebeat_mix", "id": A}]},
        {"choice": "pick", "playlists": [source(A)]},  # playlists only with playlists
        {"choice": "describe"},  # describe needs the words
        {"choice": "describe", "note": " ​\t"},  # nothing visible
        {"choice": "describe", "note": "x" * 201},
        {"choice": "later", "note": "techno"},
        {"choice": "request"},  # only the reading decides "as requested"
        {"choice": "pick", "sources": [source(A)]},  # closed: no other field
    ],
)
def test_an_answer_that_does_not_parse_is_refused(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MusicAnswer.model_validate(body)


def test_the_note_is_cleaned_and_capped() -> None:
    got = MusicAnswer(choice="describe", note="  Dark‮ techno\x00\n no vocals ​")
    assert got.note == "Dark techno  no vocals"
    assert MusicAnswer(choice="pick", note="x" * 200).note == "x" * 200


def test_a_stored_decision_is_consistent() -> None:
    for wrong in (
        {"state": "undecided", "choice": "pick"},
        {"state": "decided"},
        {"state": "decided", "choice": "pick", "sources": [source(A)]},
        {"state": "decided", "choice": "playlists"},
        {"state": "decided", "choice": "describe"},
        {"state": "decided", "choice": "request", "note": "techno"},
        {"state": "undecided", "note": "techno"},
        {"state": "decided", "choice": "playlists", "sources": [source(A), source(A)]},
        {"state": "maybe"},
    ):
        with pytest.raises(ValidationError):
            MusicDecision.model_validate(wrong)


# --- what the song search takes ---------------------------------------------------------------


def test_the_shared_playlists_replace_the_requests_and_any_other_choice_keeps_them() -> None:
    requested = [MusicSource.model_validate(source(A))]
    shared = answered(MusicAnswer.model_validate({"choice": "playlists", "playlists": [source(B)]}))
    assert [s.id for s in fill_sources(shared, requested) or []] == [B]
    for kept in (DEFAULT, answered(MusicAnswer(choice="pick"))):
        assert fill_sources(kept, requested) == requested
        assert fill_sources(kept, None) is None


def test_the_note_follows_the_request_in_the_brief_text_and_is_never_cut() -> None:
    brief = Brief.model_validate({"length_min": 30, "intensity": "moderate", "text": "Hills"})
    assert brief_with_note(brief, None) is brief
    noted = brief_with_note(brief, "Dark techno")
    assert noted.text == f"Hills {NOTE_LABEL}Dark techno"
    assert (noted.length_min, noted.intensity) == (30, "moderate")
    long = brief.model_copy(update={"text": "y" * MAX_BRIEF_TEXT})
    note = "z" * music_decision.MAX_NOTE
    cut = brief_with_note(long, note)
    assert len(cut.text) <= MAX_BRIEF_TEXT
    assert cut.text.endswith(NOTE_LABEL + note)
    bare = brief_with_note(brief.model_copy(update={"text": ""}), "Dark techno")
    assert bare.text == NOTE_LABEL + "Dark techno"


# --- the reading of a composer request with no Spotify (``Offer.composed``) ---------------------


def test_a_composer_reading_takes_a_pick_a_playlist_and_a_taste_without_spotify() -> None:
    options = (MusicOption("playlist", "Climb mix", B),)
    taste = {"hits": {"value": True, "origin": "explicit"}}
    plain = Offer(options=options, song_picking=True)
    assert plain.composed(False) is plain
    composed = plain.composed(True)
    assert (composed.spotify, composed.pick_ready) == (True, True)
    picked = resolve(read(music_taste=taste), "A class with hits", composed)
    assert (picked.music, picked.brief.music_taste is not None) == ({"source": "pick"}, True)
    named = resolve(read(music={"value": 0, "origin": "explicit"}), "Climb mix", composed)
    assert named.music == {"source": "mowl_spotify_playlist", "spotify_playlist_id": B}
    before = resolve(read(music_taste=taste), "A class with hits", plain)
    assert (before.music, before.brief.music_taste) == (None, None)  # the older flow: as today
    off = Offer(options=options, song_picking=False).composed(True)
    assert off.pick_ready is False  # song picking off: still no pick
