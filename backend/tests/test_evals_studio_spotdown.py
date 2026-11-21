"""SD-P5 (#1302; ADR 0040 section 9, ADR 0012): the studio's smoke case with **spotdown's maps**:
the same synthetic tracks as ``evals.studio.studio_tracks`` (tempo, downbeat grid, a drop every 16
bars, a quiet and a loud half), written as spotdown ``analysis-v1`` documents of the played bytes
and turned into track maps only through ``studio_map_model.studio_map`` (``timing_class_policy``
path (a), then ``spotdown_map_policy``). The real studio stage designs the class and its mix from
them against the recorded ``FakeProvider`` script and passes every design and studio check, as on
the local maps. No model, no network, no database.

This is the CI fixture run, not the quality gates: the studio gates of plan section 7 on the
owner's real corpus (#1298) come before any default switch."""

import copy
import json
from collections.abc import Mapping
from typing import Any

import pytest
from mowl import track

from agentfake import CONFIG, from_dict
from classbeat.agent.studio_mix import StudioTrack
from classbeat.design.track_map_policy import valid_track_map
from classbeat.evals.studio import load_studio_cases, run_studio_case, studio_tracks
from classbeat.spotdown.studio_map_model import StudioMap, studio_map
from spotdownfake import example
from test_evals_studio_smoke import script

CASES = load_studio_cases()


def document(local: Mapping[str, Any], n: int) -> dict[str, Any]:
    """spotdown's analysis of the n-th synthetic track: the local map's facts, as analysis-v1."""
    song = local["songs"][0]
    duration, bpm, first = float(local["duration"]), float(song["bpm"]), song["first_downbeat"]
    beat = 60.0 / bpm
    beats = [round(first + beat * k, 4) for k in range(int((duration - first) / beat))]
    half = duration / 2
    doc = copy.deepcopy(example("analysis-v1.minimal.json"))
    doc["analysis_id"] = f"an_studio{n:04d}"
    doc["provenance"]["audio"] |= {"sha256": f"{n:064x}", "file_duration_s": duration}
    doc["signal"] |= {"duration_s": duration, "content_start_s": 0.0, "content_end_s": duration}
    seconds = int(duration)
    doc["signal"]["energy"]["full"]["values"] = [0.2 if t < half else 0.9 for t in range(seconds)]
    doc["tempo"]["bpm"] = bpm
    doc["grid"] = {
        "beats": {"times_s": beats, "confidence": 0.9, "method": "estimated"},
        "downbeats": {"times_s": beats[::4], "confidence": 0.8, "method": "estimated"},
    }
    doc["structure"]["segments"] = [
        {"id": "s1", "start_s": 0.0, "end_s": half, "boundary_confidence": 0.8,
         "method": "estimated"},
        {"id": "s2", "start_s": half, "end_s": duration, "boundary_confidence": 0.8,
         "method": "estimated"},
    ]  # fmt: skip
    doc["events"] = [
        {"kind": "drop", "start_s": e["t"], "strength": 0.9, "confidence": 0.8,
         "method": "estimated"}
        for e in local["events"]
    ]  # fmt: skip
    return doc


def spotdown_tracks(count: int) -> tuple[StudioTrack, ...]:
    out: list[StudioTrack] = []
    for n, local in enumerate(studio_tracks(count)):
        doc = document(local.music_map, n)
        mapped = studio_map(doc, f"trk-studio-{n:02d}", f"{n:064x}", track.TRACK_VERSION)
        assert isinstance(mapped, StudioMap), mapped
        assert valid_track_map(mapped.track_map, track.TRACK_VERSION)
        assert mapped.provenance["timing"]["class"] == "exact"  # type: ignore[index]
        out.append(StudioTrack(local.title, local.artist, mapped.track_map))
    return tuple(out)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_the_case_passes_on_spotdowns_maps(case: Any) -> None:
    tracks = spotdown_tracks(case.music.tracks)
    local = studio_tracks(case.music.tracks)
    for mapped, own in zip(tracks, local, strict=True):  # the same music, as spotdown saw it
        assert mapped.music_map["duration"] == own.music_map["duration"]
        assert mapped.music_map["songs"][0]["bpm"] == own.music_map["songs"][0]["bpm"]
        assert len(mapped.music_map["events"]) == len(own.music_map["events"])
    fake = from_dict(script(case.music.tracks))

    def sent() -> str:
        return json.dumps([repr(s) for s in fake.sent], ensure_ascii=False)

    result = await run_studio_case(case, fake, CONFIG, sent, tracks)
    assert result.outcome == "submitted"
    assert result.passed, [(c.name, c.detail) for c in result.checks if not c.ok]
    assert {c.name for c in result.checks} >= {"mix_order", "mix_windows", "mix_total", "duration"}
