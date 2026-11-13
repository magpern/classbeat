"""Section energy in what the model sees (#1035; ADR 0010 note of 2026-11-13).

``track_detail`` (``get_music(track=i)``) shows each section's ``energy`` and ``pool_view``
(``get_pool``) a track's ``sections`` as ``[from_sec, to_sec, energy]``; both are numbers from the
map's own ranking, clamped to 0..1 and rounded, never text and never audio. The allowlists below
name the new fields, so any other key fails them.
"""

import copy
import dataclasses
import json
import math
from itertools import pairwise
from typing import Any

import pytest

from agentfake import CONFIG, analysed, brief
from classbeat.agent import choose_songs, design, model_input, revise, running, section_energy
from classbeat.agent.mix_input import track_detail as mix_track_detail
from classbeat.agent.songs import KeptSong, PickPlan, PoolTrack, analysed_pool, pool_view
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


def raw(energies: list[float], length: float = 30.0) -> list[dict[str, Any]]:
    return [
        {"track": 1, "start": length * n, "end": length * (n + 1), "energy": e}
        for n, e in enumerate(energies)
    ]


@pytest.mark.parametrize(("count", "length"), [(9, 30.0), (12, 20.0), (10, 25.0), (14, 18.0)])
def test_a_typical_track_keeps_eight_spans_not_one(count: int, length: float) -> None:
    energies = [(n % 4) / 3 for n in range(count)]
    spans = section_energy.pool_sections(raw(energies, length), 0.0, round(count * length))
    assert len(spans) == min(count, section_energy.MAX_SECTIONS) == 8
    assert spans[0][0] == 0
    assert spans[-1][1] == round(count * length)
    assert all(a[1] == b[0] for a, b in pairwise(spans))  # contiguous, nothing lost


def test_a_track_with_few_sections_keeps_each_one() -> None:
    spans = section_energy.pool_sections(raw([0.0, 0.5, 1.0]), 0.0, 90)
    assert spans == ((0, 30, 0.0), (30, 60, 0.5), (60, 90, 1.0))


def test_merging_keeps_a_quiet_intro_and_a_loud_chorus_apart() -> None:
    energies = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.9, 1.0, 1.0]  # 11 sections of 30 s
    spans = section_energy.pool_sections(raw(energies), 0.0, 330)
    assert len(spans) == 8
    assert spans[0][2] < 0.2
    assert spans[-1][2] >= 0.95
    assert max(e for *_, e in spans) - min(e for *_, e in spans) > 0.7


def test_merged_energy_is_weighted_by_duration() -> None:
    sections = [
        {"track": 1, "start": 0.0, "end": 30.0, "energy": 1.0},
        {"track": 1, "start": 30.0, "end": 40.0, "energy": 0.0},
        *[
            {"track": 1, "start": 40.0 + 60 * n, "end": 100.0 + 60 * n, "energy": 0.5}
            for n in range(7)
        ],
    ]
    spans = section_energy.pool_sections(sections, 0.0, 460)
    assert len(spans) == 8
    assert spans[0] == (0, 40, 0.75)  # (1.0 x 30 + 0 x 10) / 40


def test_the_cap_is_enforced_for_a_very_long_section_list() -> None:
    spans = section_energy.pool_sections(raw([(n % 5) / 4 for n in range(40)], 10.0), 0.0, 400)
    assert len(spans) == section_energy.MAX_SECTIONS  # fails if the reduction loop is removed
    assert spans[0][0] == 0
    assert spans[-1][1] == 400


def test_the_slice_to_the_cap_is_not_what_bounds_the_spans() -> None:
    """Without the merge loop a slice would keep 8 spans but lose the end of the track."""
    spans = section_energy.pool_sections(raw([0.1] * 20 + [1.0] * 4, 10.0), 0.0, 240)
    assert len(spans) == 8
    assert spans[-1][1] == 240
    assert spans[-1][2] == 1.0


@pytest.mark.parametrize("bad", [None, True, "0.5", math.nan, math.inf, [0.5]])
def test_a_bad_energy_is_left_out_of_a_pool_span_before_any_arithmetic(bad: object) -> None:
    sections = raw([0.2, 0.4, 0.6, 0.8, 1.0, 0.0, 0.3, 0.5, 0.7, 0.9])
    sections[3]["energy"] = bad
    spans = section_energy.pool_sections(sections, 0.0, 300)
    assert 1 <= len(spans) <= section_energy.MAX_SECTIONS
    assert all(isinstance(e, float) and 0.0 <= e <= 1.0 for *_, e in spans)
    assert all(not math.isnan(e) for *_, e in spans)


def test_an_out_of_range_energy_is_clamped_on_the_pool_path() -> None:
    spans = section_energy.pool_sections(raw([-3.0, 7.0]), 0.0, 60)
    assert spans == ((0, 30, 0.0), (30, 60, 1.0))


def pool_ctx(monkeypatch: pytest.MonkeyPatch, copies: int) -> choose_songs.ChooseContext:
    tracks = [
        dataclasses.replace(t, title=f"{t.title} {n}" * 3, sections=SPANS8)
        for n in range(copies)
        for t in pool_of(monkeypatch)[:1]
    ]
    return choose_songs.ChooseContext.build(PickPlan.build(brief()), tracks, 0)


SPANS8 = tuple((30 * n, 30 * n + 30, round(n / 7, 2)) for n in range(8))


def test_sections_are_stripped_before_any_track_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = pool_ctx(monkeypatch, 100)
    full = len(json.dumps([pool_view(t, i) for i, t in enumerate(ctx.tracks)]).encode())
    assert full > choose_songs.MAX_POOL_BYTES  # the spans alone push this pool over the cap
    out = ctx.pool_answer()
    assert len(out["tracks"]) == 100
    assert "truncated" not in out
    first_cut = out["sections_omitted_from_track"]
    assert 0 < first_cut < 100
    assert all("sections" in t for t in out["tracks"][:first_cut])
    assert all("sections" not in t for t in out["tracks"][first_cut:])
    size = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= choose_songs.MAX_POOL_BYTES


TOP_KEYS = {"tracks", "omitted_no_bpm"}


def test_the_get_pool_answer_has_exactly_the_pinned_top_level_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = pool_ctx(monkeypatch, 10).pool_answer()
    assert set(plain) == TOP_KEYS
    stripped = pool_ctx(monkeypatch, 100).pool_answer()
    assert set(stripped) == TOP_KEYS | {"sections_omitted_from_track"}
    first = stripped["sections_omitted_from_track"]
    assert type(first) is int
    assert 0 <= first < len(stripped["tracks"])
    ctx = pool_ctx(monkeypatch, 100)
    cut = dataclasses.replace(  # long titles: the cap cuts tracks even without spans
        ctx,
        tracks=[dataclasses.replace(t, title="x" * 100, artist="y" * 60) for t in ctx.tracks * 3],
    )
    truncated = cut.pool_answer()
    assert set(truncated) <= TOP_KEYS | {"truncated", "sections_omitted_from_track"}
    assert "truncated" in truncated
    assert set(truncated["truncated"]) == {"tracks_shown", "tracks_total"}
    shown = truncated["truncated"]["tracks_shown"]
    assert type(shown) is int
    assert shown == len(truncated["tracks"]) < 300
    if "sections_omitted_from_track" in truncated:
        assert type(truncated["sections_omitted_from_track"]) is int
        assert 0 <= truncated["sections_omitted_from_track"] < shown


def test_the_replace_mode_answer_adds_only_the_kept_songs_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = KeptSong(0, "K" * 22, "kept title", "kept artist", "warmup", 100.0, 240)
    plan = PickPlan.build(brief(), replace=["climb"], kept=[kept])
    tracks = [dataclasses.replace(t, sections=SPANS8) for t in pool_of(monkeypatch)[:1]]
    out = choose_songs.ChooseContext.build(plan, tracks, 0).pool_answer()
    assert set(out) == TOP_KEYS | {"kept_songs"}
    assert len(out["kept_songs"]) == 1
    assert "sections" not in out["kept_songs"][0]  # a kept song shows no spans


def fitted(shown: int, limit_tracks: int) -> dict[str, Any]:
    """``fit_pool_answer`` on ``shown`` tracks, the first without spans, cut to ``limit_tracks``
    whole tracks (the limit is that answer's size with both added keys: the cut lands there)."""

    def answer(count: int) -> dict[str, Any]:
        views = [{"index": i, "title": "t" * 200} for i in range(count)]
        for v in views[1:]:  # track 0 has no spans: the stripping stops at track 1
            v["sections"] = [[0, 30, 0.5]] * 8
        return {"tracks": views, "omitted_no_bpm": 0}

    target = answer(limit_tracks)
    for v in target["tracks"][1:]:
        v.pop("sections")
    target["sections_omitted_from_track"] = 1  # present while the cut is measured
    target["truncated"] = {"tracks_shown": limit_tracks, "tracks_total": shown}
    limit = len(json.dumps(target, ensure_ascii=False, separators=(",", ":")).encode())
    out = answer(shown)
    section_energy.fit_pool_answer(out, limit, shown)
    return out


def test_a_stale_sections_omitted_key_is_dropped_when_its_track_was_cut() -> None:
    out = fitted(shown=4, limit_tracks=1)  # the key would name track 1, which is gone
    assert len(out["tracks"]) == 1
    assert out["truncated"] == {"tracks_shown": 1, "tracks_total": 4}
    assert "sections_omitted_from_track" not in out


def test_the_sections_omitted_key_stays_when_its_track_was_not_cut() -> None:
    out = fitted(shown=4, limit_tracks=2)  # track 1 is the last one shown: index 1 < 2 tracks
    assert len(out["tracks"]) == 2
    assert out["sections_omitted_from_track"] == 1


def test_a_pool_that_fits_keeps_every_span(monkeypatch: pytest.MonkeyPatch) -> None:
    out = pool_ctx(monkeypatch, 10).pool_answer()
    assert "sections_omitted_from_track" not in out
    assert all(t["sections"] == [list(s) for s in SPANS8] for t in out["tracks"])


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
