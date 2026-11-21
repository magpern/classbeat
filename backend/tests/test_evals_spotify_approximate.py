"""SD-P6 stage D (#1303; ADR 0040 sections 2 and 3, ADR 0012): the smoke set's Spotify classes
designed on an **all-``approximate``** map, as a spotdown-first song pick gives the design stage.

Each case's synthetic playlist (``evals.music.build_music``, MOWL's map) has every track's sections
replaced by spotdown's and every track stamped ``approximate``, through the production pure path
of phase 2 (``song_sections_calc``: the recorded analysis ``verified`` against its track,
``spotdown_map_policy.reference_sections``, ``apply_track``). The fake spotdown documents describe
the same sections as MOWL's (the same spans, loudness steps and energy ranks), so the music is
the same and only the timing class differs. Then:

- every case passes against its recorded ``FakeProvider`` script, as on MOWL's map: the design
  stays valid when every track is ``approximate``;
- the model sees each track's class (``"timing": "approximate"`` and the note);
- the design validator refuses a beat-, bar- or drop-precise cue on that map, and takes the same
  draft on MOWL's map (``exact``).

No model, no network, no database. This is the CI fixture run, not the quality gates: the Spotify
gates of plan section 7 (#1298) come before any default switch.
"""

import copy
import json
from typing import Any

import pytest

from agentfake import CONFIG
from classbeat.agent import DesignContext
from classbeat.agent import track_timing_calc as tt
from classbeat.agent.design import check
from classbeat.agent.model_input import MusicData
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.agent.timing_input import TIMING_NOTE
from classbeat.evals import runner
from classbeat.evals.cases import Case, load_cases
from classbeat.evals.music import build_music
from classbeat.spotdown.song_sections_calc import Recorded, apply_track, reference_id, verified
from classbeat.spotdown.spotdown_map_policy import ReferenceSections, reference_sections
from classbeat.spotdown.timing_class_policy import TimingDecision
from smokescripts import good_draft, provider_for_case, script_for
from songanalysisdocs import youtube_doc

CASES = [c for c in load_cases() if not c.soundless]
BY_NAME = {c.name: c for c in CASES}
DECISION = TimingDecision("approximate", "matched_recording", {"residual_s": 0.3})
PRECISE = ["3, 2, 1, go!", "Push for 4 bars", "Sprint on the drop!"]


def _document(m: dict[str, Any], index: int) -> dict[str, Any]:
    """spotdown's analysis of track ``index``: MOWL's section spans, loudness steps and BPM."""
    track = m["tracks"][index]
    secs = [s for s in m["songs"] if s["track"] == index + 1]
    t0, sec = float(secs[0]["start"]), float(track["sec"])
    doc = youtube_doc(f"vidSmoke{index:03d}", float(track["bpm"] or 120))
    doc["analysis_id"] = f"an_smoke{index:04d}"
    doc["provenance"]["audio"]["sha256"] = f"{index + 1:064x}"
    doc["alignment"]["reference"] = {
        "kind": "spotify", "id": reference_id(str(track["spotify_id"])), "duration_s": sec,
    }  # fmt: skip
    spans = [(round(s["start"] - t0, 3), round(s["end"] - t0, 3), s.get("loudness")) for s in secs]
    doc["structure"]["segments"] = [
        {"id": f"s{k}", "start_s": a, "end_s": b, "boundary_confidence": 0.8,
         "method": "estimated"}
        for k, (a, b, _) in enumerate(spans)
    ]  # fmt: skip
    # MOWL's loudness L (dB) as spotdown's normalised energy: 1.5 dB per 0.05 (LOUDNESS_SCALE),
    # so the steps, and so the drops, breakdowns and energy ranks, are the same.
    level = [0.5 if loud is None else (float(loud) + 15.0) / 30.0 for _, _, loud in spans]
    values = [next(lv for (a, b, _), lv in zip(spans, level, strict=True) if a <= t < b or
                   (a, b) == spans[-1][:2]) for t in range(int(sec))]  # fmt: skip
    for band in ("full", "percussive", "low_band"):
        doc["signal"]["energy"][band]["values"] = values
    doc["events"] = []
    return doc


def all_approximate(data: MusicData) -> MusicData:
    """``data`` with every track on spotdown's sections and stamped, as phase 2 makes it."""
    m = copy.deepcopy(dict(data.analysis["map"]))
    for index, track in enumerate(list(m["tracks"])):
        doc = _document(m, index)
        rec = Recorded(index, doc["analysis_id"], doc["provenance"]["audio"]["sha256"], DECISION,
                       "youtube")  # fmt: skip
        assert verified(doc, rec, str(track["spotify_id"]), float(track["sec"]))
        mapped = reference_sections(doc, DECISION)
        assert isinstance(mapped, ReferenceSections), mapped
        made = apply_track(m, index, DECISION, "youtube", mapped.sections)
        assert made is not None
        m = made
    assert tt.classes(m) == dict.fromkeys(range(len(m["tracks"])), "approximate")
    return MusicData({**data.analysis, "map": m}, data.playlist_id, data.unavailable)


def _same_music(a: dict[str, Any], b: dict[str, Any]) -> None:
    """The same spans and events; only the loudness level (and the stamp) differ."""
    keep = ("track", "start", "end", "bpm", "energy")
    assert [{k: s[k] for k in keep} for s in a["songs"]] == [
        {k: s[k] for k in keep} for s in b["songs"]
    ]
    assert a["events"] == b["events"]


@pytest.fixture
def approximate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "build_music", lambda spec: all_approximate(build_music(spec)))  # pyright: ignore[reportUnknownLambdaType]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_the_approximate_map_is_the_same_music(case: Case) -> None:
    mowl = build_music(case.music)
    _same_music(all_approximate(mowl).analysis["map"], mowl.analysis["map"])


@pytest.mark.usefixtures("approximate")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_the_case_passes_on_an_all_approximate_map(case: Case) -> None:
    fake = provider_for_case(case)
    result = await runner.run_case(case, fake, CONFIG)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert result.passed
    assert fake.remaining == 0
    assert result.turns == len(script_for(case).turns)
    first = fake.sent[0].spec.first_message
    assert '"timing":"approximate"' in first
    assert json.dumps(TIMING_NOTE)[1:-1] in first


def _with_cue(draft: dict[str, Any], text: str) -> WorkoutDraft:
    d = copy.deepcopy(draft)
    d["segments"][0]["intervals"][0]["cue"] = text  # at 0:00, a section start of track 0
    return WorkoutDraft.model_validate(d)


@pytest.mark.parametrize("text", PRECISE)
def test_the_validator_refuses_a_precise_cue_on_the_approximate_map(text: str) -> None:
    case = BY_NAME["default_30"]
    mowl = build_music(case.music)
    draft = _with_cue(good_draft(case), text)
    check(draft, DesignContext.build(case.brief, mowl).map)  # MOWL's map (exact): taken
    with pytest.raises(ToolError) as caught:
        check(draft, DesignContext.build(case.brief, all_approximate(mowl)).map)
    assert caught.value.code == "invalid_draft"
    assert caught.value.messages


def test_a_plain_cue_on_a_section_start_is_taken_on_the_approximate_map() -> None:
    case = BY_NAME["default_30"]
    approx = DesignContext.build(case.brief, all_approximate(build_music(case.music))).map
    check(_with_cue(good_draft(case), "Settle in, zone 2"), approx)
