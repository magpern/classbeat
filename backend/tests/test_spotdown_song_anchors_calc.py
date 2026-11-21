"""MOWL's sections as anchors for the timing class of a Spotify song (ADR 0040 amendment "spotdown
is the only analysis path", item 3; #1354): ``song_anchors_calc.anchor_check`` and its use in
``pick_outcome.outcome_of`` (a ``disagree`` makes the song missing). Pure: no database."""

import pytest

from classbeat.agent.choose_songs import ChosenSong
from classbeat.songs.pick_analyse_batch import Got
from classbeat.songs.pick_outcome import Missing, outcome_of
from classbeat.spotdown.match_model import Candidate
from classbeat.spotdown.song_analysis_record_model import SongOutcome
from classbeat.spotdown.song_anchors_calc import anchor_check
from songanalysisdocs import youtube_doc

DOC = youtube_doc("vid00000001", 120.0)  # boundaries at 0, 60, 120, 180 and 240 s; offset 0


def sections(*starts: int) -> list[tuple[int, int, float]]:
    return [(s, s + 30, 0.5) for s in starts]


@pytest.mark.parametrize(
    ("starts", "expected"),
    [
        ((60, 120, 180), "agree"),  # every anchor has a boundary
        ((61, 118, 182), "agree"),  # within 3 s
        ((30, 90, 150, 210), "disagree"),  # none has one
        ((60, 90, 150, 210), "disagree"),  # 1 of 4: below the share
        ((60, 120, 150, 210), "inconclusive"),  # 2 of 4: between the two limits
        ((60, 120), "inconclusive"),  # too few anchors
        ((), "inconclusive"),  # MOWL has no sections for the song
    ],
)
def test_the_share_of_anchors_with_a_boundary_decides(
    starts: tuple[int, ...], expected: str
) -> None:
    assert anchor_check(DOC, sections(*starts), 240.0) == expected


def test_a_document_without_an_alignment_is_inconclusive() -> None:
    doc = youtube_doc("vid00000001", 120.0, alignment=False)
    assert anchor_check(doc, sections(30, 90, 150, 210), 240.0) == "inconclusive"


def test_the_offset_moves_the_boundaries_to_the_reference_clock() -> None:
    doc = youtube_doc("vid00000001", 120.0)
    doc["alignment"]["offset_s"] = -10.0  # type: ignore[index]
    doc["provenance"]["alignment"]["offset_s"] = -10.0  # type: ignore[index]
    assert anchor_check(doc, sections(50, 110, 170), 240.0) == "agree"  # 60 - 10, 120 - 10, ...
    assert anchor_check(doc, sections(60, 120, 180), 240.0) == "disagree"  # the unshifted ones


SONG = ChosenSong("SongPick00000000000001", "T", "A", "climb", 120.0, 240, True)
SOURCE = Candidate("youtube:vid00000001", 0.92, "confident", 240.0, 0.0, "ok", None, None)


def test_agreeing_or_inconclusive_anchors_keep_the_timing() -> None:
    for starts in ((60, 120, 180), (), (60, 120)):
        made = outcome_of(SONG, Got(DOC, SOURCE), sections(*starts))
        assert isinstance(made, SongOutcome)
        assert made.analysed is not None
        assert made.analysed.decision.timing_class == "approximate"


def test_disagreeing_anchors_make_the_song_missing_not_a_mowl_timing() -> None:
    made = outcome_of(SONG, Got(DOC, SOURCE), sections(30, 90, 150, 210))
    assert isinstance(made, Missing)
    assert made.reason == "anchors_disagree"


def test_an_analysis_that_never_came_is_missing_with_its_reason() -> None:
    assert outcome_of(SONG, Got(reason="not_found"), sections(60)) == Missing("not_found")
    assert outcome_of(SONG, Got(), sections(60)) == Missing("no_analysis")
