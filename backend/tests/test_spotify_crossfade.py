"""The Spotify crossfade is MOWL's 9.54 s on every route (#1427, ADR 0006 amendment).

The vendored library builds and converts its maps at its own 9.0 (``mowl.spotify.CROSSFADE_SEC``);
classbeat puts every map it fits, summarises, previews or saves into
``crossfade_calc.CROSSFADE_SEC`` and leaves a stored map, list or revision as it was. Pure: no
database, no network.
"""

import copy
import json
from typing import Any

import pytest
from mowl import spotify

from agentfake import analysed, brief, good_draft
from classbeat import crossfade_calc
from classbeat.agent import DesignContext, songs
from classbeat.agent.beat_one import Correction
from classbeat.agent.design import check
from classbeat.agent.length_measure import SONG_MEASURE
from classbeat.agent.mix_input import SpotifyData, music_map, with_corrections
from classbeat.agent.model_input import MusicData
from classbeat.agent.model_input import music_map as spotify_map
from classbeat.agent.schemas import WorkoutDraft
from classbeat.crossfade_calc import CROSSFADE_SEC, track_shift_sec, with_classbeat
from classbeat.player_class_calc import PickedSong, milliseconds, player_class
from classbeat.songs.draft_list_policy import whole_list
from classbeat.songs.playlist_list_policy import chosen_list
from classbeat.songs.preview_model import song_times
from classbeat.songs.song_list_model import SongListContent, content_hash

LIBRARY: float = spotify.CROSSFADE_SEC  # 9.0: what the wheel builds and converts at


def twelve(crossfade: float) -> dict[str, Any]:
    """A 12-track playlist's map as the library builds it, at ``crossfade``."""
    return spotify.build_map(
        [
            {
                "Title": f"T{i}",
                "Artist": "A",
                "Duration": 240_000,
                "BPM": 120,
                "SpotifyTrackID": f"SmokeSynth{i:012d}",
            }
            for i in range(12)
        ],
        {},
        crossfade,
    )


def starts(m: dict[str, Any]) -> dict[int, float]:
    first: dict[int, float] = {}
    for s in m["songs"]:
        first.setdefault(int(s["track"]), float(s["start"]))
    return first


def stored_forms(data: MusicData) -> dict[str, MusicData]:
    """The same analysis as each route can meet it: fresh from the library (9.0), a cached one
    (a stored 9.0 key), one stored before the key existed, and one already at classbeat's."""
    fresh = data.analysis["map"]
    assert fresh["crossfade"] == LIBRARY
    legacy = spotify.with_crossfade(copy.deepcopy(fresh), 0.0)
    del legacy["crossfade"]

    def of(m: dict[str, Any]) -> MusicData:
        return MusicData({**data.analysis, "map": m}, data.playlist_id, data.unavailable)

    return {
        "fresh": data,
        "cached": of(copy.deepcopy(fresh)),
        "no key": of(legacy),
        "already": of(spotify.with_crossfade(copy.deepcopy(fresh), CROSSFADE_SEC)),
    }


def test_the_constant_is_what_mowl_sends() -> None:
    assert CROSSFADE_SEC == 9.54
    assert milliseconds(CROSSFADE_SEC) == 9540  # CrossFadeInMilliSeconds of the #580 capture
    assert songs.CROSSFADE_SEC == CROSSFADE_SEC  # the importers keep working
    assert SONG_MEASURE.crossfade_sec == CROSSFADE_SEC
    assert LIBRARY == 9.0  # the vendored wheel is not rebuilt: classbeat converts after it


def test_the_last_of_twelve_tracks_starts_5_94_seconds_earlier() -> None:
    old, new = twelve(LIBRARY), with_classbeat(twelve(LIBRARY))
    assert new["crossfade"] == CROSSFADE_SEC
    for n in range(1, 13):
        assert starts(new)[n] == pytest.approx(starts(old)[n] - (n - 1) * 0.54, abs=0.002)
    assert starts(old)[12] - starts(new)[12] == pytest.approx(5.94, abs=0.002)
    assert new["duration"] == pytest.approx(old["duration"] - 11 * 0.54, abs=0.002)
    assert with_classbeat(new) is new  # already there: nothing moves
    assert track_shift_sec(old, 12) == pytest.approx(-5.94)


def test_a_stored_map_is_not_rewritten() -> None:
    stored = twelve(LIBRARY)
    before = json.dumps(stored, sort_keys=True)
    with_classbeat(stored)
    spotify_map(stored)
    assert json.dumps(stored, sort_keys=True) == before


@pytest.mark.parametrize("form", ["fresh", "cached", "no key", "already"])
def test_every_route_reaches_the_model_checks_and_save_at_9_54(
    form: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = stored_forms(analysed(monkeypatch))[form]
    corrected = with_corrections(data, (Correction(2, 1),))
    assert isinstance(corrected, SpotifyData)
    for m in (
        spotify_map(data.analysis["map"]),  # what the pool's tracks and the length use
        music_map(data),  # design, revise (DesignContext), the composer's re-time
        music_map(corrected),  # refit and a class with beat-one corrections
        DesignContext.build(brief(), data).map,
        DesignContext.build(brief(), corrected).map,
    ):
        assert m["crossfade"] == CROSSFADE_SEC
    ctx = DesignContext.build(brief(), data)
    assert ctx.summary["crossfade_sec"] == CROSSFADE_SEC
    result = check(WorkoutDraft.model_validate(good_draft()), ctx.map)  # preview, submit and save
    assert result.crossfade_sec == CROSSFADE_SEC
    assert result.map_version == ctx.map["version"]


def test_every_stored_form_gives_the_same_map() -> None:
    forms = stored_forms(MusicData({"source": "spotify", "map": twelve(LIBRARY)}, 1))
    maps = {name: spotify_map(f.analysis["map"]) for name, f in forms.items()}
    assert {json.dumps(m, sort_keys=True) for m in maps.values()} == {
        json.dumps(maps["already"], sort_keys=True)
    }
    assert maps["fresh"]["crossfade"] == CROSSFADE_SEC


def test_the_song_strip_spans_are_read_in_9_54() -> None:
    old = twelve(LIBRARY)
    want = [s.start for s in song_times(with_classbeat(old))]
    assert [s.start for s in song_times(old)] == want
    assert want[11] == pytest.approx(starts(old)[12] - 5.94, abs=0.002)


def test_a_new_list_has_9_54_and_its_total_is_the_lengths_less_9_54_per_change() -> None:
    m = twelve(CROSSFADE_SEC)
    tracks = m["tracks"]
    whole = whole_list({"tracks": [{**t, "spotify_id": t["spotify_id"]} for t in tracks]})
    assert not isinstance(whole, str)
    assert whole.crossfade_sec == CROSSFADE_SEC
    assert whole.total_sec == round(12 * 240 - 11 * 9.54)
    assert songs.total_sec([240] * 12) == pytest.approx(12 * 240 - 11 * 9.54)
    picked = chosen_list(m, target_sec=2700)
    assert picked is not None
    assert picked.crossfade_sec == CROSSFADE_SEC


def test_the_10_percent_check_uses_9_54() -> None:
    lengths = [240] * 8  # 1920 - 7 * 9.54 = 1853.22
    total = songs.total_sec(lengths)
    assert songs.within_tolerance(total, 2059)  # 1853.22 is just inside 10 % under 2059
    assert not songs.within_tolerance(total, 2060)  # at 9.0 (1857 s) 2063 would have passed
    assert songs.within_tolerance(total, 1700)
    assert songs.within_tolerance(total, 1685)  # 1853.22 is just inside 10 % over 1685
    assert not songs.within_tolerance(total, 1684)


def test_a_stored_list_at_9_0_loads_and_hashes_as_before() -> None:
    raw: dict[str, Any] = {
        "v": 2,
        "mode": "pick",
        "target_sec": 1800,
        "total_sec": 1857,
        "crossfade_sec": 9.0,
        "songs": [
            {
                "spotify_id": f"SmokePick{i:013d}",
                "title": f"Song {i}",
                "artist": "A",
                "role": "warmup",
                "bpm": 120.0,
                "length_sec": 240,
                "new": True,
                "cover": None,
            }
            for i in range(8)
        ],
        "omitted_no_bpm": 0,
        "note": "",
    }
    content = SongListContent.model_validate(raw)
    assert content.crossfade_sec == 9.0
    dumped = content.model_dump(mode="json", exclude_none=False)
    assert dumped["crossfade_sec"] == 9.0
    assert content_hash(dumped) == content_hash(content.model_dump(mode="json"))
    assert content_hash(raw) == content_hash(raw)
    # the hash covers the 9.0: it is not silently turned into 9.54
    assert content_hash({**raw, "crossfade_sec": 9.54}) != content_hash(raw)


def test_the_player_gets_9540_and_the_strip_windows() -> None:
    picked = [PickedSong(f"S{i}", "A", 240, 120.0, f"SmokeSynth{i:012d}") for i in range(12)]
    draft: dict[str, Any] = {
        "name": "x",
        "description": "",
        "segments": [
            {
                "name": "Main",
                "kind": "main",
                "intervals": [
                    {"sec": 2000, "zone": 3, "rpm": [80, 90], "standing": False, "cue": ""}
                ],
            }
        ],
    }
    made = player_class(draft, "cycling", "spotify", CROSSFADE_SEC, picked)
    assert made.crossfade_ms == 9540
    for n, window in enumerate(made.songs):  # the strip: lengths before it less 9.54 per change
        assert window.start_ms == round(n * (240_000 - 9540))
    assert made.songs[-1].end_ms == 2_000_000


def test_the_modules_name_the_constant_once() -> None:
    assert crossfade_calc.CROSSFADE_SEC == 9.54
