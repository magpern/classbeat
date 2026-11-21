"""SD-P6 stage B (#1303, ADR 0040 section 6, plan section 4a): the analysis stage's record and its
timing marks (``song_analysis_record_model``), and the refinement's spare choice
(``song_refine_calc``)."""

import json

import pytest

from classbeat.spotdown.provenance_read_calc import AudioIdentity
from classbeat.spotdown.song_analysis_record_model import (
    KEY,
    Analysed,
    RefineSwap,
    SongOutcome,
    analysis_record,
    timing_marks,
)
from classbeat.spotdown.song_refine_calc import Slot, spare_for
from classbeat.spotdown.timing_class_policy import TimingDecision
from classbeat.spotdown.track_fit_policy import Fit

AUDIO = AudioIdentity("a" * 64, "fp", "mp3", 44100.0, 2.0, 240.0, 239.0)


def analysed(cls: str = "approximate") -> Analysed:
    decision = TimingDecision(cls, "matched_recording", {"residual_s": 0.3})  # pyright: ignore[reportArgumentType]
    return Analysed("an_0001", ("spotdown", "1.0.0", None), AUDIO, decision)


def test_the_record_keeps_what_section_4a_names_and_nothing_else() -> None:
    outcomes = {
        0: SongOutcome("spotdown", None, analysed(), Fit(True, ())),
        2: SongOutcome("sections", "alignment_failed"),
        3: SongOutcome("spotdown", None, analysed(), Fit(False, ("bpm_unusable",)), warning=True),
    }
    swaps = {1: RefineSwap("SpotifyId0000000000001", "T", "A", ("too_short",), 1)}
    record = analysis_record(outcomes, swaps, rounds=1)[KEY]
    first = record["songs"][0]
    assert set(first) == {
        "position", "use", "analysis_id", "analyzer", "audio", "source_kind", "timing", "fit",
    }  # fmt: skip
    assert first["timing"] == {
        "class": "approximate", "reason": "matched_recording", "evidence": {"residual_s": 0.3},
        "policy_version": "1",
    }  # fmt: skip
    assert record["songs"][1] == {"position": 2, "use": "sections", "reason": "alignment_failed"}
    assert record["songs"][2]["warning"] is True
    assert record["swaps"] == [
        {"position": 1, "original": {"spotify_id": "SpotifyId0000000000001", "title": "T",
         "artist": "A"}, "reasons": ["too_short"], "round": 1}
    ]  # fmt: skip
    text = json.dumps(record)
    for never in ("youtube:", "ref", "cache_scope", "client_ref", "url", "job"):
        assert never not in text


def test_timing_marks_are_strict() -> None:
    record = analysis_record(
        {
            0: SongOutcome("spotdown", None, analysed(), Fit(True, ())),
            1: SongOutcome("sections", "residual_too_large", analysed("unusable")),
            2: SongOutcome("spotdown", None, analysed("exact"), Fit(True, ())),
        },
        {},
        rounds=0,
    )
    assert timing_marks(record) == [(0, "approximate", "youtube")]
    assert timing_marks({}) == []
    assert timing_marks({KEY: "nonsense"}) == []
    broken = {KEY: {"songs": [None, {"use": "spotdown", "position": True,
                                     "timing": {"class": "approximate"}}]}}  # fmt: skip
    assert timing_marks(broken) == []


def slot(n: int, length: int = 240, title: str | None = None) -> Slot:
    return Slot(f"id{n}", title or f"Song {n}", "Band", length)


LISTED = [slot(n) for n in range(8)]  # 8 x 240 s less 7 crossfades: 1857 s, target 1800


def test_the_first_spare_that_keeps_the_total_is_chosen() -> None:
    spares = [slot(8, length=400), slot(9)]
    assert spare_for(2, LISTED, spares, set(), 1800) == 1  # 400 s would break the 10 %


@pytest.mark.parametrize(
    ("spares", "tried"),
    [
        ([slot(3)], set()),  # already in the list
        ([slot(9)], {"id9"}),  # in the list before (replaced or tried)
        ([slot(9, title="Song 4")], set()),  # the same song as another one of the list
        ([], set()),
    ],
)
def test_no_spare_qualifies(spares: list[Slot], tried: set[str]) -> None:
    assert spare_for(2, LISTED, spares, tried, 1800) is None


def test_the_replaced_song_itself_may_share_its_title() -> None:
    assert spare_for(2, LISTED, [slot(9, title="Song 2")], set(), 1800) == 0
