"""SD-P7a (#1304; ADR 0040 sections 3, 5, 6 and 9): a SwedeBeat mix's map from spotdown, pure.

- ``mix_map_policy.mix_map``: the songs read as spotdown's harness reads them for the mix gates
  (content edges and trusted structure boundaries; the tempo at each song's midpoint), only
  trusted features, a span without a beat joining its neighbour, beat 1 an estimate unless a
  trusted downbeat says so, the library's map shape (``mix_map_shape_policy.valid_map``);
- ``mix_map_model.mix_map_of``: path (a) only, and the provenance record (no mix id, ``source.ref``
  null, the mix policy's version), ``current_mix_provenance`` the cache's rule;
- the fixture form of the plan section 7 mix gates: on documents whose features sit where the
  songs change, every song start and end is within 2 s and every BPM within 2 %;
- the settings: gone (#1353); the one config is not usable by default and the worker sets it.
"""

import copy
import json
from typing import Any
from uuid import UUID

import pytest
from mowl import music

from classbeat.config import Settings
from classbeat.design.mix_map_shape_policy import valid_map
from classbeat.spotdown import mix_map_policy, studio_tracks_config
from classbeat.spotdown.mix_map_model import (
    MIX_POLICY_VERSIONS,
    MixMap,
    MixRefused,
    current_mix_provenance,
    mix_map_of,
)
from classbeat.spotdown.spotdown_map_policy import MapRefused
from classbeat.spotdown.timing_class_policy import TimingDecision
from spotdownfake import example

MIX = "planted-mix-id-77"
SHA = "c" * 64
OWNER = UUID("0b6f1a52-6f0e-4f43-9a51-6c1d2a0c9e11")
EXACT = TimingDecision("exact", "played_recording", {})


def _feature(**values: Any) -> dict[str, Any]:
    return {"confidence": 0.9, "method": "estimated", **values}


def mix_document(
    songs: list[tuple[float, float, float]],
    *,
    sha: str = SHA,
    boundary_confidence: float = 0.8,
    change_confidence: float = 0.8,
) -> dict[str, Any]:
    """spotdown's analysis of a mix whose songs are ``(start, end, bpm)``: a boundary and a tempo
    change point where each song starts, beats at each song's tempo, a drop mid-song."""
    duration = songs[-1][1]
    doc = copy.deepcopy(example("analysis-v1.minimal.json"))
    doc["provenance"]["audio"] |= {"sha256": sha, "file_duration_s": duration}
    doc["signal"] |= {"duration_s": duration, "content_start_s": 0.0, "content_end_s": duration}
    doc["signal"]["energy"]["full"]["values"] = [0.5] * int(duration)
    beats: list[float] = []
    for a, b, bpm in songs:
        step, t = 60.0 / bpm, a
        while t < b:
            beats.append(round(t, 4))
            t += step
    doc["tempo"] |= {
        "bpm": songs[0][2],
        "change_points": [
            {"time_s": a, "bpm": bpm, "confidence": change_confidence, "method": "estimated"}
            for a, _b, bpm in songs[1:]
        ],
    }
    doc["grid"] = {"beats": _feature(times_s=beats)}
    doc["structure"]["segments"] = [
        {"id": f"s{n}", "start_s": a, "end_s": b, "boundary_confidence": boundary_confidence,
         "method": "estimated"}
        for n, (a, b, _bpm) in enumerate(songs)
    ]  # fmt: skip
    doc["events"] = [
        _feature(kind="drop", start_s=(a + b) / 2, strength=0.9) for a, b, _bpm in songs
    ] + [_feature(kind="build_up", start_s=a + 1.0, end_s=a + 2.0, strength=0.5)
         for a, _b, _bpm in songs]  # fmt: skip
    return doc


SONGS = [(0.0, 200.0, 124.0), (200.0, 410.0, 128.0), (410.0, 600.0, 132.0)]


def mapped(doc: dict[str, Any]) -> dict[str, Any]:
    result = mix_map_policy.mix_map(doc, EXACT, music.MAP_VERSION)
    assert isinstance(result, mix_map_policy.MappedMix), result
    assert valid_map(result.music_map, music.MAP_VERSION)
    return result.music_map


# --- mix_map ------------------------------------------------------------------------------------


def test_songs_follow_the_content_edges_and_trusted_boundaries_with_their_tempo() -> None:
    m = mapped(mix_document(SONGS))
    assert m["version"] == music.MAP_VERSION
    assert m["duration"] == 600.0
    assert [(s["song"], s["start"], s["end"], s["bpm"]) for s in m["songs"]] == [
        (1, 0.0, 200.0, 124.0),
        (2, 200.0, 410.0, 128.0),
        (3, 410.0, 600.0, 132.0),
    ]
    for song in m["songs"]:
        assert set(song) == {
            "song", "start", "end", "bpm", "first_downbeat", "first_phrase", "phrase_bars",
            "grid_score", "beat1_margin",
        }  # fmt: skip
        assert song["start"] <= song["first_downbeat"] < song["end"]
        assert song["first_phrase"] == song["first_downbeat"]
        assert song["phrase_bars"] == 8
        assert song["grid_score"] == 0.9


def test_only_drops_and_breakdowns_become_events_of_their_song() -> None:
    m = mapped(mix_document(SONGS))
    assert [(e["kind"], e["t"], e["song"]) for e in m["events"]] == [
        ("drop", 100.0, 1),
        ("drop", 305.0, 2),
        ("drop", 505.0, 3),
    ]
    assert all(e["precise"] == e["t"] for e in m["events"])


def test_an_event_outside_every_song_is_left_out() -> None:
    doc = mix_document(SONGS)
    doc["signal"] |= {"content_start_s": 2.0, "content_end_s": 598.0}
    doc["events"] += [
        _feature(kind="drop", start_s=1.0, strength=0.9),  # before the content start
        _feature(kind="breakdown", start_s=598.0, strength=0.9),  # at the content end
        _feature(kind="breakdown", start_s=599.5, strength=0.9),  # after it
        _feature(kind="breakdown", start_s=597.9, strength=0.9),  # the last song's own
    ]
    m = mapped(doc)
    assert [(e["kind"], e["t"], e["song"]) for e in m["events"]] == [
        ("drop", 100.0, 1),
        ("drop", 305.0, 2),
        ("drop", 505.0, 3),
        ("breakdown", 597.9, 3),
    ]


def test_an_untrusted_boundary_or_change_point_is_left_out() -> None:
    m = mapped(mix_document(SONGS, boundary_confidence=0.3))
    assert [(s["start"], s["end"]) for s in m["songs"]] == [(0.0, 600.0)]
    m = mapped(mix_document(SONGS, change_confidence=0.3))
    assert [s["bpm"] for s in m["songs"]] == [124.0, 124.0, 124.0]
    doc = mix_document(SONGS)
    for segment in doc["structure"]["segments"]:
        segment["method"] = "heuristic"
    assert len(mapped(doc)["songs"]) == 1


def test_the_bpm_is_the_tempo_at_the_songs_midpoint() -> None:
    doc = mix_document(SONGS)
    doc["tempo"]["change_points"][0]["time_s"] = 350.0  # after song 2's midpoint (305)
    assert [s["bpm"] for s in mapped(doc)["songs"]] == [124.0, 124.0, 132.0]


def test_a_span_without_a_beat_joins_its_neighbour() -> None:
    doc = mix_document(SONGS)
    beats = doc["grid"]["beats"]["times_s"]
    doc["grid"]["beats"]["times_s"] = [t for t in beats if not 200.0 <= t < 410.0]
    assert [(s["start"], s["end"]) for s in mapped(doc)["songs"]] == [(0.0, 410.0), (410.0, 600.0)]
    doc["grid"]["beats"]["times_s"] = [t for t in beats if t >= 200.0]
    assert [(s["start"], s["end"]) for s in mapped(doc)["songs"]] == [(0.0, 410.0), (410.0, 600.0)]


def test_beat_one_is_an_estimate_unless_a_trusted_downbeat_says_so() -> None:
    doc = mix_document(SONGS)
    assert {s["beat1_margin"] for s in mapped(doc)["songs"]} == {0.0}  # warns, as a doubtful vote
    doc["grid"]["downbeats"] = {"times_s": [1.0, 201.0, 411.0], "confidence": 0.4,
                                "method": "heuristic"}  # fmt: skip
    first = mapped(doc)["songs"]
    assert {s["beat1_margin"] for s in first} == {0.0}
    assert first[0]["first_downbeat"] == 0.0  # the first beat
    doc["grid"]["downbeats"] |= {"confidence": 0.8, "method": "estimated"}
    trusted = mapped(doc)["songs"]
    assert [s["first_downbeat"] for s in trusted] == [1.0, 201.0, 411.0]
    assert {s["beat1_margin"] for s in trusted} == {mix_map_policy.TRUSTED_MARGIN}


def test_the_content_edges_bound_the_songs_when_trusted() -> None:
    doc = mix_document(SONGS)
    doc["signal"] |= {"content_start_s": 2.0, "content_end_s": 598.0}
    songs = mapped(doc)["songs"]
    assert (songs[0]["start"], songs[-1]["end"]) == (2.0, 598.0)
    doc["signal"]["method"] = "heuristic"
    songs = mapped(doc)["songs"]
    assert (songs[0]["start"], songs[-1]["end"]) == (0.0, 600.0)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda d: d["signal"].pop("duration_s"), "no_duration"),
        (lambda d: d["tempo"].update(confidence=0.2), "bpm_untrusted"),
        (lambda d: d["tempo"].update(method="heuristic"), "bpm_untrusted"),
        (lambda d: d["grid"]["beats"].update(confidence=0.2), "no_beats"),
        (lambda d: d["grid"]["beats"].update(times_s=[]), "no_beats"),
        (lambda d: d["grid"]["beats"].update(times_s=["x"]), "no_beats"),
    ],
)
def test_a_document_without_trusted_basics_is_refused(change: Any, reason: str) -> None:
    doc = mix_document(SONGS)
    change(doc)
    assert mix_map_policy.mix_map(doc, EXACT, music.MAP_VERSION) == MapRefused(reason)


def test_too_many_songs_or_events_are_refused() -> None:
    songs = [(float(n), float(n + 1), 120.0) for n in range(mix_map_policy.MAX_SONGS + 1)]
    doc = mix_document(songs)
    assert mix_map_policy.mix_map(doc, EXACT, 13) == MapRefused("too_many_features")
    doc = mix_document(SONGS)
    doc["events"] = [
        _feature(kind="drop", start_s=n * 0.25, strength=0.5)
        for n in range(mix_map_policy.MAX_EVENTS + 1)
    ]
    assert mix_map_policy.mix_map(doc, EXACT, 13) == MapRefused("too_many_features")


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (TimingDecision("approximate", "matched", {}), "not_exact"),
        (TimingDecision("unusable", "x", {}), "not_exact"),
        (TimingDecision("exact", "beat_equivalent", {}), "not_the_played_clock"),
    ],
)
def test_only_the_played_mix_is_mapped(decision: TimingDecision, reason: str) -> None:
    assert mix_map_policy.mix_map(mix_document(SONGS), decision, 13) == MapRefused(reason)


# --- the section 7 mix gates on fixtures ----------------------------------------------------------

GATE_MIXES = [
    [(0.0, 245.0, 126.0), (245.0, 480.0, 130.0), (480.0, 731.5, 134.0), (731.5, 960.0, 128.0)],
    [(0.0, 300.0, 90.0), (300.0, 612.0, 172.0), (612.0, 905.0, 150.0)],
    [(0.0, 180.0, 64.0), (180.0, 420.0, 118.0), (420.0, 600.0, 140.0), (600.0, 840.0, 176.0)],
    [(0.0, 410.0, 125.0), (410.0, 830.0, 125.0)],
    [(0.0, 222.2, 100.0), (222.2, 444.4, 110.0), (444.4, 666.6, 120.0), (666.6, 888.8, 130.0)],
]


def test_the_mapped_songs_meet_the_mix_gates_on_the_fixture_mixes() -> None:
    """Plan section 7, mixes: song start and end within 2 s on at least 95 %, BPM per song within
    2 % on at least 95 %. On fixtures whose features sit where the songs change, classbeat's
    reading of spotdown's document must not lose what the harness gates. (The real-music gate run
    is the owner's, #1298, and comes before any default switch.)"""
    edges, bpms = [], []
    for songs in GATE_MIXES:
        m = mapped(mix_document(songs))
        got = m["songs"]
        assert len(got) == len(songs)
        for (a, b, bpm), song in zip(songs, got, strict=True):
            edges += [abs(song["start"] - a) <= 2.0, abs(song["end"] - b) <= 2.0]
            bpms.append(abs(song["bpm"] - bpm) <= 0.02 * bpm)
    assert sum(edges) / len(edges) >= 0.95
    assert sum(bpms) / len(bpms) >= 0.95


# --- mix_map_of and the provenance --------------------------------------------------------------


def test_the_played_mix_is_exact_and_keeps_its_provenance_without_the_mix_id() -> None:
    doc = mix_document(SONGS)
    result = mix_map_of(doc, MIX, SHA, music.MAP_VERSION)
    assert isinstance(result, MixMap), result
    assert result.music_map == mapped(doc)
    record = result.provenance
    assert record["timing"]["class"] == "exact"  # type: ignore[index]
    assert record["timing"]["reason"] == "played_recording"  # type: ignore[index]
    assert record["source"] == {"kind": "upload", "ref": None}
    assert record["audio"]["sha256"] == SHA  # type: ignore[index]
    assert record["policy_versions"] == dict(MIX_POLICY_VERSIONS)
    assert dict(MIX_POLICY_VERSIONS)["mix_map_policy"] == mix_map_policy.POLICY_VERSION
    assert MIX not in json.dumps(record)
    assert current_mix_provenance(record)


def test_a_document_of_other_bytes_or_another_source_is_never_used() -> None:
    other = mix_map_of(mix_document(SONGS, sha="d" * 64), MIX, SHA, 13)
    assert other == MixRefused("not_played_recording")
    doc = mix_document(SONGS)
    doc["provenance"]["source"] = {"kind": "youtube", "ref": "dQw4w9WgXcQ"}
    assert mix_map_of(doc, MIX, SHA, 13) == MixRefused("not_played_recording")
    doc = mix_document(SONGS)
    del doc["provenance"]["audio"]
    assert mix_map_of(doc, MIX, SHA, 13) == MixRefused("no_audio_identity")


def test_a_refused_map_or_an_oversized_record_is_a_refusal() -> None:
    doc = mix_document(SONGS)
    doc["tempo"]["confidence"] = 0.1
    assert mix_map_of(doc, MIX, SHA, 13) == MixRefused("bpm_untrusted")
    doc = mix_document(SONGS)
    doc["provenance"]["analyzer"]["name"] = None
    assert mix_map_of(doc, MIX, SHA, 13) == MixRefused("no_provenance")


def test_an_oversized_record_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from classbeat.spotdown import mix_map_model

    monkeypatch.setattr(mix_map_model, "PROVENANCE_MAX", 64)
    assert mix_map_of(mix_document(SONGS), MIX, SHA, 13) == MixRefused("provenance_too_large")


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(record_version=99),
        lambda r: r["timing"].update({"class": "approximate"}),
        lambda r: r["timing"].update(reason="beat_equivalent"),
        lambda r: r["policy_versions"].update(mix_map_policy="0"),
        lambda r: r["policy_versions"].pop("mix_map_policy"),
        lambda r: r.pop("timing"),
    ],
)
def test_a_record_of_another_build_is_not_current(change: Any) -> None:
    result = mix_map_of(mix_document(SONGS), MIX, SHA, 13)
    assert isinstance(result, MixMap)
    record = copy.deepcopy(result.provenance)
    change(record)
    assert not current_mix_provenance(record)
    assert not current_mix_provenance(None)
    assert not current_mix_provenance("record")


# --- the settings -------------------------------------------------------------------------------


def test_the_mix_settings_are_gone_and_the_shared_config_is_not_usable_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert not hasattr(settings, "spotdown_mix")
    assert not hasattr(settings, "spotdown_mix_owners")
    assert studio_tracks_config.current() == studio_tracks_config.TracksConfig()
    assert studio_tracks_config.current().refusal() == "not_configured"


# --- the worker's wiring --------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "refusal"), [("k" * 40, None), (None, "not_configured")])
def test_the_worker_configures_the_one_config_from_its_settings(
    key: str | None, refusal: str | None
) -> None:
    from pydantic import SecretStr

    from classbeat import egress_spotdown
    from classbeat.worker.__main__ import configure_spotdown

    secret = None if key is None else SecretStr(key)
    settings = Settings(_env_file=None, app_secret_key=secret)  # type: ignore[call-arg]
    egress_spotdown.install_spotdown("http://spotdown-api:8000", SecretStr("k" * 32))
    try:
        configure_spotdown(settings)
        config = studio_tracks_config.current()
        assert (config.scope_key is not None) is (key is not None)
        assert config.refusal() == refusal
    finally:
        egress_spotdown.install_spotdown(None, None)
        studio_tracks_config.configure(None)


def test_the_worker_logs_once_at_start_that_analysis_is_unavailable_and_no_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from classbeat.worker.__main__ import configure_spotdown

    caplog.set_level("INFO")
    try:
        configure_spotdown(Settings(_env_file=None))  # type: ignore[call-arg]
    finally:
        studio_tracks_config.configure(None)
    lines = [r.getMessage() for r in caplog.records if "music analysis" in r.getMessage()]
    assert lines == ["music analysis is unavailable: spotdown is not installed in this worker"]
