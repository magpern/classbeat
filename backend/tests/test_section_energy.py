"""Section energy in what the model sees (#1035; ADR 0010 note of 2026-11-13).

``track_detail`` (``get_music(track=i)``) shows each section's ``energy`` and ``pool_view``
(``get_pool``) a track's ``sections`` as ``[from_sec, to_sec, energy]``; both are numbers from the
map's own ranking, clamped to 0..1 and rounded, never text and never audio. The allowlists below
name the new fields, so any other key fails them.
"""

import copy
import json
import math
from itertools import pairwise
from typing import Any

import pytest

from agentfake import CONFIG, analysed, brief
from classbeat.agent import choose_songs, design, model_input, revise, running, section_energy
from classbeat.agent.mix_input import track_detail as mix_track_detail
from classbeat.agent.songs import PickPlan, PoolTrack, analysed_pool, pool_view
from classbeat.evals import song_checks
from classbeat.evals.pick import run_pick_case
from classbeat.evals.suite import load_suite
from pickfake import CHOOSE_LIMITS, POOL_LIMITS
from songscripts import provider_for

SECTION_KEYS = {"from", "to", "bpm", "energy"}
POOL_KEYS = {
    "index",
    "title",
    "artist",
    "role",
    "bpm",
    "length_sec",
    "drops",
    "breakdowns",
    "sections",
}


def synthetic_map(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return model_input.music_map(analysed(monkeypatch).analysis["map"])


def test_a_track_detail_section_carries_a_bounded_rounded_energy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = synthetic_map(monkeypatch)
    detail = model_input.track_detail(m, 0)
    assert detail["sections"]
    for section in detail["sections"]:
        assert set(section) == SECTION_KEYS
        energy = section["energy"]
        assert isinstance(energy, float)
        assert 0.0 <= energy <= 1.0
        assert energy == round(energy, 2)
    assert {s["energy"] for s in detail["sections"]} >= {0.0, 1.0}  # the ranking spans the track


@pytest.mark.parametrize("bad", [None, "loud", True, math.nan, math.inf, [1]])
def test_an_energy_that_is_not_a_finite_number_is_left_out(
    monkeypatch: pytest.MonkeyPatch, bad: Any
) -> None:
    m = synthetic_map(monkeypatch)
    m["songs"][0]["energy"] = bad
    first = model_input.track_detail(m, 0)["sections"][0]
    assert set(first) == SECTION_KEYS - {"energy"}
    assert "loud" not in json.dumps(model_input.track_detail(m, 0))


def test_an_energy_outside_zero_to_one_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    m = synthetic_map(monkeypatch)
    m["songs"][0]["energy"] = 7.3
    m["songs"][1]["energy"] = -2
    m["songs"][2]["energy"] = 0.12345
    energies = [s["energy"] for s in model_input.track_detail(m, 0)["sections"][:3]]
    assert energies == [1.0, 0.0, 0.12]


def test_a_map_without_energy_shows_none_and_a_mix_never_has_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    m = synthetic_map(monkeypatch)
    for s in m["songs"]:
        s.pop("energy", None)  # a map analysed before section energy
    assert all("energy" not in s for s in model_input.track_detail(m, 1)["sections"])
    assert all("energy" not in s for s in mix_track_detail(m, 1)["sections"])


def pool_of(monkeypatch: pytest.MonkeyPatch) -> list[PoolTrack]:
    data = analysed(monkeypatch)
    wanted = [(str(t["spotify_id"]), "build") for t in data.analysis["map"]["tracks"]]
    tracks, omitted = analysed_pool(data.analysis["map"], wanted)
    assert omitted == 0
    return tracks


def test_a_pool_track_shows_its_sections_as_numbers_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracks = pool_of(monkeypatch)
    view = pool_view(tracks[0], 0)
    assert set(view) == POOL_KEYS
    spans = view["sections"]
    assert 1 <= len(spans) <= section_energy.MAX_SECTIONS
    for begin, end, energy in spans:
        assert isinstance(begin, int)
        assert isinstance(end, int)
        assert 0 <= begin < end <= view["length_sec"]
        assert isinstance(energy, float)
        assert 0.0 <= energy <= 1.0
        assert energy == round(energy, 2)
    assert spans[0][0] == 0  # seconds from the start of the track
    assert all(a[1] == b[0] for a, b in pairwise(spans))


def test_a_pool_track_without_energy_has_no_sections_key(monkeypatch: pytest.MonkeyPatch) -> None:
    data = analysed(monkeypatch)
    m = copy.deepcopy(dict(data.analysis["map"]))
    for s in m["songs"]:
        s.pop("energy", None)
    wanted = [(str(t["spotify_id"]), "build") for t in m["tracks"]]
    tracks, _ = analysed_pool(m, wanted)
    assert all("sections" not in pool_view(t, i) for i, t in enumerate(tracks))


def test_a_track_with_many_sections_is_folded_to_the_bound() -> None:
    sections = [
        {"track": 1, "start": 10.0 * n, "end": 10.0 * (n + 1), "energy": (n % 5) / 4}
        for n in range(40)
    ]
    spans = section_energy.pool_sections(sections, 0.0, 400)
    assert 1 <= len(spans) <= section_energy.MAX_SECTIONS
    assert spans[0][0] == 0
    assert spans[-1][1] == 400


def test_the_pool_answer_still_fits_its_cap_for_a_big_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    tracks = pool_of(monkeypatch) * 15
    ctx = choose_songs.ChooseContext.build(PickPlan.build(brief()), tracks, 0)
    size = len(json.dumps(ctx.pool_answer(), separators=(",", ":")).encode())
    assert size <= choose_songs.MAX_POOL_BYTES


def test_every_stage_that_shows_energy_tells_the_model_how_to_use_it() -> None:
    for prompt in (
        design.SYSTEM_PROMPT,
        revise.SYSTEM_PROMPT,
        running.DESIGN_SYSTEM_PROMPT,
        running.REVISE_SYSTEM_PROMPT,
    ):
        assert section_energy.DESIGN_GUIDANCE in prompt
    assert section_energy.SONGS_GUIDANCE in choose_songs.SYSTEM_PROMPT
    for text in (section_energy.DESIGN_GUIDANCE, section_energy.SONGS_GUIDANCE):
        assert "warm" in text
        assert "cooldown" in text


async def test_the_section_energy_eval_case_passes_on_fakes() -> None:
    suite = load_suite()
    case = suite.song_cases["section_energy_30"]
    assert case.expect.sections_shown
    assert "section_energy_30" in suite.manifest.categories["song_choice"]["songs"]
    result, _, _ = await run_pick_case(
        case,
        provider_for(case),
        CONFIG,
        (POOL_LIMITS, CHOOSE_LIMITS),
        suite.manifest.bpm_bands,
    )
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert "sections_shown" in {c.name for c in result.checks}
    assert result.passed


def test_the_sections_shown_check_fails_when_the_model_was_not_shown_the_energy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracks = pool_of(monkeypatch)
    views = [pool_view(t, i) for i, t in enumerate(tracks)]
    sent = json.dumps({"tracks": views}, separators=(",", ":"))
    assert song_checks.sections_shown(tracks, sent)[1]
    bare = [PoolTrack(t.spotify_id, t.title, t.artist, t.role, t.bpm, t.length_sec) for t in tracks]
    assert not song_checks.sections_shown(bare, sent)[1]  # no spans on the analysed tracks
    assert not song_checks.sections_shown(tracks, sent.replace('"sections"', '"parts"'))[1]
    assert not song_checks.sections_shown([], sent)[1]
