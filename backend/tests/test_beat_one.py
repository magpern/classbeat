"""Beat-one corrections, the pure part (ADR 0007 amendment "beat-one corrections", BC1).

No database, no network: the maps are built here (a Swedebeat mix in the shape of
``music.analyze_signal`` and a Spotify playlist through the library's own ``spotify.build_map``).
"""

import copy
from typing import Any

import pytest
from mowl import spotify

from classbeat.agent import beat_one, model_input
from classbeat.agent.beat_one import Correction


def mix_map() -> dict[str, Any]:
    return {
        "version": 1,
        "duration": 240.0,
        "songs": [
            {"song": 1, "start": 0.0, "end": 120.0, "bpm": 120.0, "first_downbeat": 0.5,
             "first_phrase": 0.5, "phrase_bars": 8, "grid_score": 0.9, "beat1_margin": 2.0},
            {"song": 2, "start": 120.0, "end": 240.0, "bpm": 128.0, "first_downbeat": 120.25,
             "first_phrase": 120.25, "phrase_bars": 8, "grid_score": 0.8, "beat1_margin": 1.1},
        ],
        "events": [
            {"t": 60.5, "precise": 60.5, "kind": "drop", "strength": 0.8, "song": 1},
            {"t": 150.25, "precise": 150.25, "kind": "breakdown", "strength": 0.6, "song": 2},
        ],
    }  # fmt: skip


def spotify_map() -> dict[str, Any]:
    tracks = [
        {"SpotifyTrackID": f"{n:022d}", "Title": f"Song {n}‮", "Artist": "Band",
         "Duration": 180_000, "BPM": bpm}
        for n, bpm in ((1, 120), (2, 0), (3, 126))
    ]  # fmt: skip
    sections = {
        t["SpotifyTrackID"]: [
            {"start": 0.0, "duration": 60.0, "loudness": -12.0},
            {"start": 60.0, "duration": 120.0, "loudness": -6.0},
        ]
        for t in tracks
    }
    return model_input.music_map(spotify.build_map(tracks, sections))


def test_a_mix_correction_moves_only_that_songs_beat_one_and_events_on_a_copy() -> None:
    m = mix_map()
    before = copy.deepcopy(m)
    out = beat_one.apply(m, [Correction(song=2, beats=1)])
    assert m == before, "the input map is never changed"
    one, two = out["songs"]
    assert one == before["songs"][0]
    assert two["first_downbeat"] == pytest.approx(120.25 + 60 / 128, abs=1e-3)
    assert two["beat1_shift"] == 1
    assert out["events"][0] == before["events"][0]
    # The event of song 2 sits on the corrected bar grid.
    bar = 4 * 60 / 128
    bars = (out["events"][1]["t"] - two["first_downbeat"]) / bar
    assert abs(bars - round(bars)) < 0.01


def test_a_negative_correction_moves_beat_one_earlier() -> None:
    out = beat_one.apply(mix_map(), [Correction(song=1, beats=-2)])
    assert out["songs"][0]["first_downbeat"] == pytest.approx(0.5 - 2 * 0.5, abs=1e-3)


def test_a_spotify_correction_moves_every_section_of_that_track_only() -> None:
    m = spotify_map()
    out = beat_one.apply(m, [Correction(song=3, beats=1)])
    for old, new in zip(m["songs"], out["songs"], strict=True):
        if old["track"] == 3:
            assert new["first_downbeat"] == pytest.approx(
                old["first_downbeat"] + 60 / 126, abs=1e-3
            )
            assert new["beat1_shift"] == 1
        else:
            assert new == old


def test_a_song_the_map_lacks_is_skipped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    m = mix_map()
    with caplog.at_level("WARNING", logger="classbeat.agent.beat_one"):
        assert beat_one.apply(m, [Correction(song=9, beats=1)]) == m
    [record] = caplog.records
    assert getattr(record, "skipped", None) == 1


def test_songs_are_at_most_200_with_titles_of_at_most_100_characters() -> None:
    tracks = [
        {"SpotifyTrackID": f"{n:022d}", "Title": "x" * 300, "Artist": "a", "Duration": 60_000,
         "BPM": 120}
        for n in range(1, 251)
    ]  # fmt: skip
    m = model_input.music_map(spotify.build_map(tracks, {}))
    out = beat_one.songs(m, 100_000)
    assert len(out) == beat_one.MAX_SONGS == 200
    assert all(len(s["title"]) == 100 for s in out)
    assert all(isinstance(s[k], float) for s in out for k in ("start", "end", "bpm"))


def test_corrections_apply_once_each_whatever_the_order() -> None:
    a = beat_one.apply(mix_map(), [Correction(2, 1), Correction(1, -1), Correction(2, 1)])
    b = beat_one.apply(mix_map(), [Correction(1, -1), Correction(2, 1)])
    assert a == b
    assert beat_one.as_list([Correction(2, 1), Correction(1, -1)]) == [
        {"song": 1, "beats": -1},
        {"song": 2, "beats": 1},
    ]


@pytest.mark.parametrize(
    ("song", "beats"), [(0, 1), (501, 1), (1, 0), (1, 4), (1, -4), (True, 1), (1, 1.0)]
)
def test_a_correction_outside_the_range_is_refused(song: Any, beats: Any) -> None:
    with pytest.raises(ValueError, match="must be"):
        Correction(song=song, beats=beats)


def test_the_songs_of_a_mix_name_the_estimates_until_corrected() -> None:
    m = mix_map()
    assert beat_one.songs(m, 300) == [
        {"song": 1, "start": 0.0, "end": 120.0, "bpm": 120.0, "estimate": False},
        {"song": 2, "start": 120.0, "end": 240.0, "bpm": 128.0, "estimate": True},
    ]
    corrected = beat_one.songs(beat_one.apply(m, [Correction(2, 1)]), 300)
    assert corrected[1]["estimate"] is False
    assert [s["song"] for s in beat_one.songs(m, 100)] == [1], "only songs the class plays"


def test_the_songs_of_a_spotify_playlist_are_its_tracks_with_clean_titles() -> None:
    m = spotify_map()
    out = beat_one.songs(m, 10_000)
    assert [s["song"] for s in out] == [1, 2, 3]
    assert [s["estimate"] for s in out] == [False, True, False]  # track 2 had no BPM
    assert out[0]["title"] == "Song 1"  # the bidi mark is cleaned
    assert out[0]["start"] == 0.0
    assert out[1]["start"] == pytest.approx(180 - m["crossfade"], abs=0.1)
    assert out[0]["end"] == out[1]["start"]
    assert out[2]["end"] == pytest.approx(m["duration"], abs=0.1)


def test_songs_skip_an_entry_without_bpm_or_track_instead_of_failing() -> None:
    m = mix_map()
    del m["songs"][1]["bpm"]
    assert [s["song"] for s in beat_one.songs(m, 300)] == [1]
    spot = spotify_map()
    del spot["songs"][0]["track"]
    assert beat_one.songs(spot, 10_000)  # the other tracks still listed
