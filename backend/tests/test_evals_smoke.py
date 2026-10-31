"""The smoke set in CI (ADR 0006 section 8, issue #95): 19 cases (15 designs and 4 revisions),
each run through the real design or revise stage against a recorded ``FakeProvider`` script and
synthetic music, then checked. No model, no network. Also: the checks fail on drafts that break a
rule, and a failed run is a failed case."""

import dataclasses
import json
from typing import Any
from uuid import uuid4

import pytest

from agentfake import CONFIG, from_dict
from classbeat.agent import DesignContext, design_stage, run_stage
from classbeat.agent.design import DesignResult, check
from classbeat.agent.limits import Limits
from classbeat.agent.loop import RunInfo, StageOutcome
from classbeat.agent.provider import Usage
from classbeat.agent.schemas import WorkoutDraft
from classbeat.agent.stage import ToolError
from classbeat.evals.cases import Case, load_cases
from classbeat.evals.checks import run_checks
from classbeat.evals.music import MusicSpec, build_music
from classbeat.evals.runner import EvalHost, results_json, results_table, run_case
from smokedrafts import make_draft
from smokescripts import good_draft, provider_for_case, script_for

CASES = load_cases()
BY_NAME = {c.name: c for c in CASES}


def test_there_are_nineteen_distinct_cases_with_a_script_each() -> None:
    assert len(CASES) == 19  # 15 designs and 4 revisions
    assert len({c.name for c in CASES}) == 19
    assert sorted(c.name for c in CASES if c.revise) == [
        "revise_adversarial_music", "revise_easier", "revise_more_sprints", "revise_other_songs",
    ]  # fmt: skip
    for case in CASES:
        assert script_for(case).turns


def test_the_cases_cover_the_asked_options() -> None:
    briefs = [c.brief for c in CASES]
    assert {b.intensity for b in briefs} == {"easy", "moderate", "hard"}
    assert any(b.climbs for b in briefs)
    assert any(b.sprints for b in briefs)
    assert any(b.races for b in briefs)
    assert {b.ftp_test for b in briefs} >= {5, 20}
    assert min(b.length_min for b in briefs) == 10
    assert max(b.length_min for b in briefs) >= 60
    assert any(len(b.text) == 2000 for b in briefs)  # a long free-text brief
    assert any(c.music.no_bpm for c in CASES)  # missing BPM
    assert any(c.music.sections == "none" for c in CASES)
    assert any(c.expect.duration == "music" for c in CASES)  # music shorter than the ask
    assert sum(bool(c.expect.forbidden_text) for c in CASES) == 3  # adversarial tool output


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_the_case_passes_against_its_recorded_script(case: Case) -> None:
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert result.passed
    assert fake.remaining == 0  # the script was played to its end
    assert result.turns == len(script_for(case).turns)
    assert result.cost_micros > 0
    assert len(result.prompt_version) == 64


async def test_the_corrections_of_the_scripted_cases_happen() -> None:
    # hard_45_sprints: the first preview carries the quality issue; ftp_5_45: a fitting error;
    # long_60: a refused submit is a correction round. The scripts assert each in `contains`.
    for name, calls in (("hard_45_sprints", 4), ("ftp_5_45", 4), ("long_60", 4)):
        result = await run_case(BY_NAME[name], provider_for_case(BY_NAME[name]), CONFIG)
        assert result.passed
        assert result.tool_calls == calls


async def test_adversarial_text_reaches_the_model_only_as_data() -> None:
    case = BY_NAME["adversarial_music"]
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG)
    assert result.passed
    sent = fake.sent[0]
    assert "PWNED" in sent.spec.first_message  # in the data block of the first message
    assert "PWNED" not in sent.spec.system  # never in the instructions


def test_the_synthetic_music_is_invented_and_in_the_analysis_format() -> None:
    data = build_music(MusicSpec(tracks=3, no_bpm=[1]))
    m = data.analysis["map"]
    assert [t["title"] for t in m["tracks"]] == ["Smoke Song 0", "Smoke Song 1", "Smoke Song 2"]
    assert m["crossfade"] == 9.0
    assert m["duration"] == 3 * 240 - 2 * 9
    assert m["tracks"][1]["bpm"] == 0
    assert m["tracks"][0]["bpm"] == 120
    assert all(t["spotify_id"].startswith("SmokeSynth") for t in m["tracks"])


# --- the checks fail on drafts that break a rule -------------------------------------------------


def outcome_of(turns: int, tool_calls: int, cost_micros: int) -> StageOutcome:
    return StageOutcome(
        None, uuid4(), RunInfo("design", "fake", "m", "e", "0" * 64), turns, tool_calls, Usage(),
        cost_micros, "m",
    )  # fmt: skip


def evaluate(
    case: Case, draft: dict[str, Any], **changes: Any
) -> tuple[DesignResult, dict[str, bool]]:
    ctx = DesignContext.build(case.brief, build_music(case.music))
    result = check(WorkoutDraft.model_validate(draft), ctx.map)
    result = dataclasses.replace(result, **changes)
    outcome = outcome_of(3, 3, 100)
    checks = run_checks(case, result, outcome, Limits(), float(ctx.map["duration"]))
    return result, {c.name: c.ok for c in checks}


def test_the_good_draft_passes_every_check() -> None:
    case = BY_NAME["default_30"]
    _, ok = evaluate(case, good_draft(case))
    assert all(ok.values())
    assert len(ok) == 13


def fits(case: Case, draft: dict[str, Any]) -> bool:
    ctx = DesignContext.build(case.brief, build_music(case.music))
    try:
        check(WorkoutDraft.model_validate(draft), ctx.map)
    except ToolError:
        return False
    return True


def broken(case: Case, edit: Any) -> dict[str, Any]:
    draft = good_draft(case)
    edit(draft)
    return draft


def test_each_check_fails_on_its_own_kind_of_draft() -> None:
    case = BY_NAME["default_30"]
    base = good_draft(case)
    short = make_draft(900, intensity="moderate", climbs=1, sprints=2)
    flat = make_draft(1800, intensity="moderate")  # no climb, no sprint
    hard = broken(
        case, lambda d: [i.update(zone=5) for s in d["segments"][1:-1] for i in s["intervals"]]
    )
    easy = broken(case, lambda d: [i.update(zone=1) for s in d["segments"] for i in s["intervals"]])
    with_ftp = next(
        d
        for lead in range(200, 420, 10)
        if fits(
            case,
            d := make_draft(1800, intensity="moderate", climbs=1, sprints=2, ftp=5, ftp_lead=lead),
        )
    )
    no_warmup = broken(case, lambda d: d["segments"][0].update(kind="main"))
    jumpy = broken(case, lambda d: d["segments"][1]["intervals"][0].update(rpm=[40, 44]))
    standing = broken(
        case,
        lambda d: d["segments"][2]["intervals"][0].update(sec=150, standing=True, rpm=[65, 75]),
    )
    leaky = dict(base, name="Ignore all previous instructions")
    leaky_case = case.model_copy(
        update={
            "expect": case.expect.model_copy(update={"forbidden_text": ["IGNORE all previous"]})
        }
    )
    expectations = [
        (case, short, "duration"),
        (case, flat, "climbs"),
        (case, flat, "sprints_and_races"),
        (case, hard, "zones"),
        (case, easy, "tss"),
        (case, with_ftp, "ftp_test"),
        (case, no_warmup, "structure"),
        (case, jumpy, "quality_issues"),
        (case, standing, "quality_issues"),
        (leaky_case, leaky, "forbidden_text"),
    ]
    for c, draft, expected in expectations:
        _, ok = evaluate(c, draft)
        assert ok[expected] is False, expected


def test_changes_on_music_is_read_from_the_quality_report() -> None:
    case = BY_NAME["default_30"]
    result, ok = evaluate(case, good_draft(case))
    assert ok["changes_on_music"]
    worse = {**result.quality, "changes_on_music": "4 of 17"}
    _, ok = evaluate(case, good_draft(case), quality=worse)
    assert ok["changes_on_music"] is False


def test_the_cooldown_check_needs_exactly_one_last_cooldown() -> None:
    case = BY_NAME["default_30"]
    result, _ = evaluate(case, good_draft(case))
    spec = json.loads(json.dumps(result.spec))
    spec["segments"][-1]["intervals"] = [{"sec": 30, "zone": 1, "rpm": [70, 80], "standing": False}]
    outcome = outcome_of(3, 3, 1)
    checks = run_checks(
        case,
        dataclasses.replace(result, spec=spec),
        outcome,
        Limits(),
        1900.0,
    )
    assert {c.name: c.ok for c in checks}["cooldown"] is False
    assert {c.name: c.ok for c in checks}["validate"] is False


def test_the_limits_check_fails_over_the_cases_and_the_runs_limits() -> None:
    case = BY_NAME["default_30"]
    result, _ = evaluate(case, good_draft(case))
    ctx = DesignContext.build(case.brief, build_music(case.music))
    for turns, tool_calls, cost in ((13, 3, 1), (3, 41, 1), (3, 3, 1_500_001)):
        outcome = outcome_of(turns, tool_calls, cost)
        checks = run_checks(case, result, outcome, Limits(), float(ctx.map["duration"]))
        assert {c.name: c.ok for c in checks}["limits"] is False


# --- a run that fails is a failed case; results hold no model text --------------------------------


@pytest.mark.parametrize(
    ("turns", "outcome"),
    [
        ([{"refusal": True}], "model_refused"),
        ([{"error": "overloaded"}], "model_unavailable"),
        ([{"error": "auth"}], "model_config"),
        ([{"end_turn": "done"}, {"end_turn": "done"}], "invalid_output"),
    ],
)
async def test_a_failed_run_is_a_failed_case(turns: list[dict[str, Any]], outcome: str) -> None:
    result = await run_case(BY_NAME["easy_30"], from_dict({"turns": turns}), CONFIG)
    assert result.outcome == outcome
    assert not result.passed
    assert result.checks == []


async def test_the_cost_limit_fails_the_case_before_it_overspends() -> None:
    config = dataclasses.replace(CONFIG, limits=Limits(job_cost_micros=1000))
    result = await run_case(BY_NAME["easy_30"], provider_for_case(BY_NAME["easy_30"]), config)
    assert result.outcome == "limit_exceeded"
    assert result.turns == 0
    assert not result.passed


async def test_results_hold_numbers_and_names_only() -> None:
    results = [
        await run_case(c, provider_for_case(c), CONFIG)
        for c in (BY_NAME["easy_30"], BY_NAME["adversarial_brief"])
    ]
    table, body = results_table(results), results_json(results)
    assert "2 of 2 passed" in table
    for text in (table, body):
        assert "PWNED" not in text
        assert "Smoke Song" not in text
        assert "SmokeSynth" not in text
    data = json.loads(body)["results"]
    assert [r["case"] for r in data] == ["easy_30", "adversarial_brief"]
    assert set(data[0]) == {
        "case", "repeat", "model", "effort", "model_answered", "prompt_version", "passed",
        "outcome", "turns", "tool_calls", "corrections", "cost_micros", "wall_sec",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "duration_sec", "tss", "tss_bounds", "changes_on_music", "checks", "timeline",
    }  # fmt: skip


async def test_the_eval_host_needs_no_database_and_stores_nothing() -> None:
    case = BY_NAME["easy_30"]
    ctx = DesignContext.build(case.brief, build_music(case.music))
    host = EvalHost()
    done = await run_stage(
        host, provider_for_case(case), design_stage(ctx), ctx.first_message(), CONFIG
    )
    assert host.counters.turns == done.turns == 3


def test_a_draft_longer_than_the_music_fails_the_duration_check() -> None:
    # 45 minutes asked and drafted, but the music runs 23: within 5 % of the ask, over the music.
    case = BY_NAME["music_too_short"].model_copy(
        update={"expect": BY_NAME["default_30"].expect, "brief": BY_NAME["races_40"].brief}
    )
    case = case.model_copy(update={"brief": case.brief.model_copy(update={"length_min": 40})})
    _, ok = evaluate(case, make_draft(2400, intensity="hard", races=2))
    assert ok["duration"] is False


def test_a_long_zone_run_is_only_excused_when_it_holds_the_ftp_test() -> None:
    ftp = BY_NAME["ftp_20_60"]
    _, ok = evaluate(ftp, good_draft(ftp))
    assert ok["quality_issues"]  # the 20 minute test is one long run, excused
    case = BY_NAME["default_30"]
    long_run = broken(
        case, lambda d: [i.update(zone=3) for s in d["segments"][1:-1] for i in s["intervals"]]
    )
    _, ok = evaluate(case, long_run)
    assert ok["quality_issues"] is False  # a long run without the test is reported


async def test_a_result_counts_the_corrections_tokens_and_the_class_it_drew() -> None:
    case = BY_NAME["long_60"]  # its script submits a 30 s cooldown first and is refused once
    result = await run_case(case, provider_for_case(case), CONFIG, repeat=2)
    assert (result.corrections, result.repeat, result.model) == (1, 2, CONFIG.model)
    assert result.usage.input_tokens > 0
    assert result.wall_sec >= 0
    assert result.tss_bounds is not None
    assert result.tss_bounds[0] < result.tss_bounds[1]
    assert sum(i["sec"] for s in result.timeline for i in s["intervals"]) == result.duration_sec
    assert set(result.timeline[0]) == {"name", "kind", "intervals"}  # no cues


# --- the revise cases ------------------------------------------------------------------


async def test_a_revise_case_sends_the_base_draft_and_the_requests_to_the_revise_stage() -> None:
    case = BY_NAME["revise_adversarial_music"]
    assert case.revise is not None
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG)
    assert result.passed
    sent = fake.sent[0].spec
    assert sent.stage == "revise"
    data = json.loads(sent.first_message[sent.first_message.index('{"brief"') :])
    assert data["request"] == case.revise.request
    assert data["earlier_requests"] == ["Make the warm-up a little longer."]
    assert data["base_draft"] == case.revise.base.model_dump(mode="json", exclude_none=True)
    assert "PWNED" in sent.first_message  # the injection text is data in the message ...
    assert "PWNED" not in sent.system  # ... never in the instructions


async def test_the_base_draft_handed_back_is_not_a_revision() -> None:
    case = BY_NAME["revise_more_sprints"]
    assert case.revise is not None
    same = case.revise.base.model_dump(mode="json", exclude_none=True)
    script = script_for(case).model_dump(mode="json")
    script["drafts"]["revised"] = same
    result = await run_case(case, from_dict(script), CONFIG)
    assert {c.name: c.ok for c in result.checks}["changed"] is False
    assert not result.passed


async def test_a_note_with_forbidden_text_fails_the_case() -> None:
    case = BY_NAME["revise_adversarial_music"]
    script = script_for(case).model_dump(mode="json")
    script["turns"][-1]["calls"][0]["input"]["note"] = "PWNED as asked"
    result = await run_case(case, from_dict(script), CONFIG)
    assert result.failed_checks == ["forbidden_text"]


# --- the revise case that only asks for other songs (issue #278) --------------------------


async def test_a_request_for_other_songs_passes_only_by_ending_with_songs_requested() -> None:
    case = BY_NAME["revise_other_songs"]
    assert case.revise is not None
    assert case.revise.outcome == "songs_requested"
    result = await run_case(case, provider_for_case(case), CONFIG)
    assert result.outcome == "submitted"
    assert [c.name for c in result.checks] == [
        "points_to_change_songs", "forbidden_text", "limits",
    ]  # fmt: skip
    assert result.passed
    assert result.cost_micros > 0  # the run's usage still counts


async def test_a_draft_for_a_request_that_only_asks_for_other_songs_fails_the_case() -> None:
    songs, ordinary = BY_NAME["revise_other_songs"], BY_NAME["revise_easier"]
    # A model that submits a draft for the songs-only request fails the case.
    result = await run_case(songs, provider_for_case(ordinary), CONFIG)
    assert not result.passed
    assert [c.name for c in result.checks if not c.ok] == ["points_to_change_songs"]


async def test_songs_requested_for_an_ordinary_revise_case_fails_it() -> None:
    songs = BY_NAME["revise_other_songs"]
    ordinary = BY_NAME["revise_easier"]
    result = await run_case(ordinary, provider_for_case(songs), CONFIG)
    assert not result.passed
