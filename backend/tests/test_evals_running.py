"""The running eval cases (ADR 0018, ADR 0012 amendment of 2026-11-03, issue #465): the running
smoke cases, the suite's ``running`` category and the running checks. No model, no network:
``FakeProvider`` scripts (``runningscripts``) on synthetic music.

Covered: every running case passes against its recorded script on the running prompt and schema
(never the cycling one); each running check fails on its own kind of draft; a cycling-only field
from the model is refused by the running schema and the run still ends with a running draft; the
case model refuses a base or an expectation of the other sport; the cycling checks are unchanged.
"""

import copy
import dataclasses
import json
from typing import Any
from uuid import uuid4

import pytest

from agentfake import CONFIG
from classbeat.agent import DesignContext
from classbeat.agent.limits import Limits
from classbeat.agent.loop import RunInfo, StageOutcome
from classbeat.agent.provider import Usage
from classbeat.agent.running import DESIGN_SYSTEM_PROMPT, REVISE_SYSTEM_PROMPT, check_running
from classbeat.agent.schemas import RunningDraft
from classbeat.design.sport_rules import rules_for
from classbeat.evals.cases import Case, load_cases
from classbeat.evals.checks import run_checks
from classbeat.evals.music import build_music
from classbeat.evals.runner import class_shape, run_case
from classbeat.evals.suite import load_suite
from classbeat.models.versions import all_prompt_versions
from runningscripts import good_draft, provider_for_case, running_draft, script_for

SUITE = load_suite()
INDEX = SUITE.manifest.case_index()
RUNNING = sorted(
    (c for c in SUITE.design_cases.values() if c.brief.sport == "running"), key=lambda c: c.name
)
BY_NAME = {c.name: c for c in RUNNING}
SMOKE_RUNNING = {c.name for c in load_cases() if c.brief.sport == "running"}


def test_the_running_cases_are_where_the_manifest_says() -> None:
    assert {c.name for c in RUNNING} == SMOKE_RUNNING | {"cp_run_hard_start_30", "cp_run_capped_45"}
    categories = {INDEX[c.name] for c in RUNNING}
    assert categories == {("running", "design"), ("adversarial", "design"),
                          ("class_patterns", "design")}  # fmt: skip
    assert INDEX["run_adversarial"] == ("adversarial", "design")  # every run must pass
    for case in RUNNING:
        assert not set(case.brief.model_fields_set) & {"climbs", "sprints", "races", "ftp_test"}
        if case.name != "run_adversarial":  # invented music, no injected titles
            assert (case.music.name, case.music.titles) == ("Invented Smoke Mix", {})


@pytest.mark.parametrize("case", RUNNING, ids=lambda c: c.name)
async def test_the_running_case_passes_on_the_running_prompt(case: Case) -> None:
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG, zone7_tss_margin=SUITE.manifest.zone7_tss_margin)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert fake.remaining == 0
    names = {c.name for c in result.checks}
    assert "running_shape" in names
    assert not names & {"sprints_and_races", "climbs", "ftp_test", "tss"}
    assert result.tss_bounds is None  # the running load model is not calibrated
    spec = fake.sent[0].spec
    assert spec.system == (REVISE_SYSTEM_PROMPT if case.revise else DESIGN_SYSTEM_PROMPT)
    key = "running_revise" if case.revise else "running_design"
    assert result.prompt_version == all_prompt_versions()[key]
    assert '"sport":"running"' in spec.first_message
    assert all("standing" not in i for s in result.timeline for i in s["intervals"])
    assert result.cost_micros <= SUITE.manifest.groups["design"].median_cost_micros


async def test_a_cycling_field_from_the_model_is_refused_and_the_run_recovers() -> None:
    case = BY_NAME["run_bike_words_30"]
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG)
    assert result.passed
    assert result.tool_calls == 4  # the refused preview (``invalid_input``, the script's expect)


async def test_injected_running_text_reaches_the_model_only_as_data() -> None:
    case = BY_NAME["run_adversarial"]
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG)
    assert result.passed
    sent = fake.sent[0].spec
    assert "PWNED" in sent.first_message
    assert "PWNED" not in sent.system


def test_a_running_case_gets_the_running_rules_or_none() -> None:
    for name in ("run_interval_30", "run_interval_45"):
        made = rules_for(BY_NAME[name].brief)
        assert made is not None
        assert made.provenance()["profile"].startswith("running.interval.")
    for name in ("run_steady_40", "run_incline_35"):  # weak cells: no rules
        assert rules_for(BY_NAME[name].brief) is None


# --- the running checks fail on their own kind of draft ------------------------------------------


def outcome() -> StageOutcome:
    return StageOutcome(
        None, uuid4(), RunInfo("design", "fake", "m", "e", "0" * 64), 3, 3, Usage(), 100, "m"
    )


def evaluate(case: Case, draft: dict[str, Any], **changes: Any) -> dict[str, bool]:
    ctx = DesignContext.build(case.brief, build_music(case.music))
    result = check_running(RunningDraft.model_validate(draft), ctx.map)
    result = dataclasses.replace(result, **changes)
    checks = run_checks(case, result, outcome(), Limits(), float(ctx.map["duration"]))
    return {c.name: c.ok for c in checks}


def test_the_good_running_draft_passes_every_check() -> None:
    case = BY_NAME["run_interval_45"]
    ok = evaluate(case, good_draft(case))
    assert all(ok.values())
    assert set(ok) == {
        "structure", "validate", "duration", "cooldown", "running_shape", "zones",
        "changes_on_music", "quality_issues", "forbidden_text", "limits",
    }  # fmt: skip


def test_a_spec_with_a_cycling_key_or_out_of_range_value_fails_the_running_shape() -> None:
    case = BY_NAME["run_interval_30"]
    ctx = DesignContext.build(case.brief, build_music(case.music))
    result = check_running(RunningDraft.model_validate(good_draft(case)), ctx.map)
    for edit in (
        lambda s: s["segments"][1]["intervals"][0].update(rpm=[70, 80]),
        lambda s: s["segments"][1]["intervals"][0].update(standing=True),
        lambda s: s["segments"][1]["intervals"][0].update(zone=6),
        lambda s: s["segments"][1]["intervals"][0].update(gradient=0.5),
        lambda s: s.pop("sport"),
    ):
        spec = copy.deepcopy(result.spec)
        edit(spec)
        checks = run_checks(
            case, dataclasses.replace(result, spec=spec), outcome(), Limits(), 2000.0
        )
        assert {c.name: c.ok for c in checks}["running_shape"] is False


def test_a_spec_without_its_sport_fails_the_running_validation() -> None:
    case = BY_NAME["run_interval_30"]
    ctx = DesignContext.build(case.brief, build_music(case.music))
    result = check_running(RunningDraft.model_validate(good_draft(case)), ctx.map)
    spec = {k: v for k, v in result.spec.items() if k != "sport"}
    checks = run_checks(case, dataclasses.replace(result, spec=spec), outcome(), Limits(), 2000.0)
    flags = {c.name: c for c in checks}
    assert flags["validate"].ok is False
    assert "validate_running" in flags["validate"].detail


def test_an_asked_incline_must_be_there() -> None:
    case = BY_NAME["run_incline_35"]
    assert evaluate(case, good_draft(case))["incline"] is True
    flat = running_draft(case.name, case.brief.length_min * 60, intensity="moderate")
    assert evaluate(case, flat)["incline"] is False


def test_the_running_zones_are_measured_from_zone_four() -> None:
    case = BY_NAME["run_interval_45"]
    easy = good_draft(case)
    for seg in easy["segments"]:
        for i in seg["intervals"]:
            i["zone"] = min(i["zone"], 3)
    assert evaluate(case, easy)["zones"] is False  # a moderate run needs some zone 4
    hard = good_draft(case)
    for seg in hard["segments"][1:-1]:
        for i in seg["intervals"]:
            i["zone"] = 4
    assert evaluate(case, hard)["zones"] is False


def test_the_steady_runs_long_zone_is_allowed_by_its_case_only() -> None:
    case = BY_NAME["run_steady_40"]
    assert evaluate(case, good_draft(case))["quality_issues"] is True
    strict = case.model_copy(update={"expect": case.expect.model_copy(update={"allow_issues": []})})
    assert evaluate(strict, good_draft(case))["quality_issues"] is False


async def test_a_revision_that_keeps_the_hard_efforts_fails_easier() -> None:
    case = BY_NAME["run_revise_easier"]
    assert case.revise is not None
    unchanged = case.revise.base.model_dump(mode="json")
    unchanged["name"] = "Invented same run"
    result = await run_case(case, provider_for_case(case, unchanged), CONFIG)
    assert {c.name: c.ok for c in result.checks}["revision"] is False


def test_the_running_shape_holds_the_gradient_and_never_standing() -> None:
    timeline = [
        {"name": "Main", "kind": "main", "intervals": [
            {"sec": 60, "zone": 4, "gradient": 1, "track": 0, "on": "drop", "start": "00:00"},
        ]},
    ]  # fmt: skip
    assert class_shape(timeline) == [
        {"name": "Main", "kind": "main",
         "intervals": [{"sec": 60, "zone": 4, "gradient": 1, "track": 0}]},
    ]  # fmt: skip


# --- the case model keeps the sports apart -------------------------------------------------------


def _dump(name: str) -> dict[str, Any]:
    return json.loads(json.dumps(BY_NAME[name].model_dump(mode="json")))


def test_a_case_refuses_a_base_or_expectation_of_the_other_sport() -> None:
    cycling_base = SUITE.design_cases["revise_easier"].model_dump(mode="json")["revise"]["base"]
    mixed = _dump("run_revise_easier")
    mixed["revise"]["base"] = cycling_base
    with pytest.raises(ValueError, match="base draft is of its brief's sport"):
        Case.model_validate_json(json.dumps(mixed))
    cycling = SUITE.design_cases["easy_30"].model_dump(mode="json")
    cycling["expect"]["incline_min_sec"] = 60
    with pytest.raises(ValueError, match="incline_min_sec is for a running case"):
        Case.model_validate_json(json.dumps(cycling))
    games = _dump("run_interval_30")
    games["expect"]["games"] = ["racetrack"]
    with pytest.raises(ValueError, match="no games"):
        Case.model_validate_json(json.dumps(games))


def test_the_scripts_cover_every_running_case() -> None:
    for case in RUNNING:
        assert script_for(case).turns
