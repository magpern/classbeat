"""SD-P6 stage B (#1303, ADR 0040 section 6, plan section 4a): the analysis stage's record and its
timing marks (``song_analysis_record_model``), and the refinement's spare choice
(``song_refine_calc``) and state (``pick_analyse._Refine``)."""

import json
from typing import Any

import pytest

from classbeat.agent.choose_songs import ChosenSong
from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.songs import PoolTrack
from classbeat.songs import pick_analyse
from classbeat.songs.pick_match import MatchedPool
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
from classbeat.spotdown.song_swap_calc import Candidate
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


# --- the refinement's state (``pick_analyse._Refine``) ------------------------------------------


def _refine_state() -> Any:
    """A list of 8 songs, 2 of them sprints (positions 4 and 5), and 3 spare sprints s1 to s3,
    ranked in that order."""
    roles = ["warmup", "build", "climb", "recovery", "sprint", "sprint", "recovery", "cooldown"]
    listed = [f"l{n}" for n in range(8)]
    spare = ["s1", "s2", "s3"]
    picks = [PoolPick(n, r, i) for n, (i, r) in enumerate(zip(listed, roles, strict=True))]
    picks += [PoolPick(10 + n, "sprint", i) for n, i in enumerate(spare)]
    candidates = {
        p.spotify_id: Candidate(p.spotify_id, p.role, f"T {p.spotify_id}", "A", "sections",
                                "uncertain", 0.9 - p.ref / 100)
        for p in picks
    }  # fmt: skip
    matched = MatchedPool(tuple(picks), tuple(picks), candidates=candidates, active=True)
    pool = {
        p.spotify_id: PoolTrack(p.spotify_id, f"T {p.spotify_id}", "A", p.role, 120.0, 240)
        for p in picks
    }
    songs = [
        ChosenSong(i, f"T {i}", "A", r, 120.0, 240, True)
        for i, r in zip(listed, roles, strict=True)
    ]
    state = pick_analyse._Refine(matched, pool, songs)  # pyright: ignore[reportPrivateUsage]
    for n in range(8):
        state.outcomes[n] = SongOutcome("spotdown", None, None, Fit(False, ("bpm_unusable",)))
    return state


def test_a_spare_swapped_out_is_never_a_spare_again() -> None:
    state = _refine_state()
    assert state.replace(4, 1, 1800) is None  # round 1: position 4 takes s1
    assert state.songs[4].spotify_id == "s1"
    state.outcomes[4] = SongOutcome("spotdown", None, None, Fit(False, ("bpm_unusable",)))
    state.replace(4, 2, 1800)  # round 2: s1 does not fit either: s2
    state.replace(5, 2, 1800)  # the next sprint slot must not get s1 back
    assert [state.songs[4].spotify_id, state.songs[5].spotify_id] == ["s2", "s3"]
    assert state.swaps[4].original_id == "l4"  # the original as it was chosen
    assert state.swaps[4].round == 2
    assert state.replace(4, 2, 1800) is False  # nothing is left: accepted with a warning
