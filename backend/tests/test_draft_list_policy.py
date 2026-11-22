"""The list spotdown times for a ``design_draft`` on the user's MOWL playlist (#1407): every track,
in the map's order, or the category the job ends with. Pure: no database, no network."""

from typing import Any

import pytest

from classbeat.design.revision_spotdown_model import MAX_TRACKS
from classbeat.songs.draft_list_policy import GUESS_BPM, whole_list


def track(n: int, **over: Any) -> dict[str, Any]:
    made = {"track": n + 1, "spotify_id": f"PlantedTrk{n:012d}", "title": f"Song {n}",
            "artist": "A", "bpm": 120 + n, "sec": 200.4}  # fmt: skip
    return {**made, **over}


def test_every_track_is_listed_in_order_with_its_slot() -> None:
    tracks = [track(0), track(1), track(0), track(2)]  # a track listed twice stays twice
    chosen = whole_list({"tracks": tracks})
    assert not isinstance(chosen, str)
    assert [s.spotify_id for s in chosen.songs] == [t["spotify_id"] for t in tracks]
    assert [s.role for s in chosen.songs] == ["warmup", "interval", "interval", "cooldown"]
    assert [s.length_sec for s in chosen.songs] == [200] * 4
    assert {s.new for s in chosen.songs} == {False}
    assert chosen.total_sec == chosen.target_sec == 200 * 4 - 9 * 3


@pytest.mark.parametrize("bpm", [None, 0, -3, True, "120"])
def test_a_track_without_a_bpm_keeps_the_librarys_guess(bpm: Any) -> None:
    chosen = whole_list({"tracks": [track(0, bpm=bpm), track(1)]})
    assert not isinstance(chosen, str)
    assert [s.bpm for s in chosen.songs] == [GUESS_BPM, 121.0]


@pytest.mark.parametrize(
    "bad",
    [
        {"spotify_id": "localFile1"},
        {"spotify_id": None},
        {"sec": 0.5},
        {"sec": None},
        {"sec": True},
    ],
)
def test_a_track_spotdown_cannot_align_or_time_is_analysis_failed(bad: dict[str, Any]) -> None:
    assert whole_list({"tracks": [track(0), track(1, **bad)]}) == "analysis_failed"


TOO_MANY = [track(n % 10) for n in range(MAX_TRACKS + 1)]
MAPS = [{}, {"tracks": []}, {"tracks": "x"}, {"tracks": [track(0), 3]}, {"tracks": TOO_MANY}]


@pytest.mark.parametrize(
    "m", MAPS, ids=["no_tracks", "empty", "not_a_list", "not_a_track", "too_many"]
)
def test_a_map_a_revision_cannot_record_is_invalid_input(m: dict[str, Any]) -> None:
    assert whole_list(m) == "invalid_input"


def test_the_bound_itself_is_listed() -> None:
    chosen = whole_list({"tracks": [track(n % 10) for n in range(MAX_TRACKS)]})
    assert not isinstance(chosen, str)
    assert len(chosen.songs) == MAX_TRACKS
