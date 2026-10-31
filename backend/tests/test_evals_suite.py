"""The regression suite's manifest, version and digest, results format 2 and the zone 7 margin
(ADR 0012 sections 4, 4.2 and 7; issue #202). No model, no network: the runs here replay the smoke
set's recorded ``FakeProvider`` scripts."""

import copy
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from agentfake import CONFIG
from classbeat.agent import DesignContext
from classbeat.agent.design import check
from classbeat.agent.limits import Limits
from classbeat.agent.loop import RunInfo, StageOutcome
from classbeat.agent.provider import Usage
from classbeat.agent.schemas import WorkoutDraft
from classbeat.config import Settings
from classbeat.evals import checks, gate, suite
from classbeat.evals.cases import load_cases
from classbeat.evals.checks import run_checks, tss_bounds_for
from classbeat.evals.music import build_music
from classbeat.evals.pick import load_pick_cases, run_pick_case
from classbeat.evals.runner import (
    SuiteHeader,
    run_case,
    song_suite_row,
    suite_results,
    suite_results_json,
    suite_row,
)
from classbeat.evals.suite import Manifest, SuiteError, load_suite, suite_digest
from advscripts import provider_for_case as adv_provider
from pickfake import CHOOSE_LIMITS, POOL_LIMITS
from pickscripts import provider_for_case as pick_provider
from smokescripts import good_draft
from smokescripts import provider_for_case as design_provider

# Each released suite_version and its digest. Append only: a released version's digest never
# changes. Changing a case, a check, a threshold or the manifest: bump suite_version, set the
# manifest's digest to ``suite.current_digest()`` and add the new pair here.
RELEASED: dict[int, str] = {
    1: "beabe1887df6236e30bd4b040104281e78d1b02ee2d8adecca0bee32557651b5",
    2: "19bdd02a13e8e4c92f3c284b1a7cc6b1d1031d83d2292c459a0d22134a2f782a",
}

SUITE = load_suite()
MANIFEST = SUITE.manifest
# The suite's own cases (suite/<category>/*.json), by the issue that added them.
SUITE_OWN = {p.stem for p in Path(suite.__file__).parent.glob("suite/*/*.json")}


def released(version: int, digest: str) -> bool:
    """The rule the pin enforces: the newest version, with the digest it was released with."""
    return version == max(RELEASED) and RELEASED.get(version) == digest


def parts() -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str], dict[str, Any]]:
    data = suite.read_manifest_data()
    cases = {
        **{n: c.model_dump(mode="json") for n, c in SUITE.design_cases.items()},
        **{n: c.model_dump(mode="json") for n, c in SUITE.song_cases.items()},
    }
    return data, cases, suite.digest_sources(), suite.check_constants()


# --- version and digest ------------------------------------------------------------------------


def test_the_stored_digest_is_the_suites_and_its_version_is_pinned() -> None:
    assert suite.current_digest() == MANIFEST.digest
    assert suite_digest(*parts()) == MANIFEST.digest
    assert released(MANIFEST.suite_version, MANIFEST.digest), (
        "the suite changed without a new suite_version: see RELEASED"
    )


def test_a_change_without_a_bump_fails_the_pin() -> None:
    # The digest recomputed after a change, at the same version: refused.
    assert not released(MANIFEST.suite_version, "f" * 64)
    # A bumped version that was not pinned: refused too.
    assert not released(MANIFEST.suite_version + 1, "f" * 64)


def _changed(edit: Any) -> str:
    data, cases, sources, constants = copy.deepcopy(parts())
    edit(data, cases, sources, constants)
    return suite_digest(data, cases, sources, constants)


@pytest.mark.parametrize(
    "edit",
    [
        lambda d, c, s, k: c["easy_30"]["brief"].update(length_min=31),  # a case
        lambda d, c, s, k: c["pick_30"]["catalogue"].update(size=11),
        lambda d, c, s, k: k.update({"checks.DURATION_TOLERANCE": 0.06}),  # a check constant
        lambda d, c, s, k: s.update({"checks.py": s["checks.py"] + "\n# a change\n"}),  # a check
        lambda d, c, s, k: d["rules"].update(pass_share=0.8),  # a threshold
        lambda d, c, s, k: d["groups"]["design"].update(median_cost_micros=750001),
        lambda d, c, s, k: d.update(zone7_tss_margin=0.2),
        lambda d, c, s, k: d["categories"]["standard"]["design"].remove("easy_30"),  # the manifest
        lambda d, c, s, k: d.update(suite_version=d["suite_version"] + 1),
    ],
    ids=[
        "case", "song_case", "check_constant", "check_source", "pass_share", "median_cost",
        "zone7_margin", "category", "version",
    ],
)  # fmt: skip
def test_the_digest_changes_with_every_part_of_the_suite(edit: Any) -> None:
    assert _changed(edit) != MANIFEST.digest


def test_the_digest_ignores_the_stored_digest_itself() -> None:
    assert _changed(lambda d, c, s, k: d.update(digest="a" * 64)) == MANIFEST.digest


def test_a_patched_check_constant_makes_the_suite_refuse_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checks, "DURATION_TOLERANCE", 0.5)
    assert "checks.DURATION_TOLERANCE" in suite.check_constants()
    with pytest.raises(SuiteError, match="bump suite_version"):
        load_suite()


def test_a_changed_manifest_without_its_digest_refuses_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = suite.read_manifest_data()
    data["rules"]["pass_share"] = 0.5
    monkeypatch.setattr(suite, "read_manifest_data", lambda: data)
    with pytest.raises(SuiteError, match="bump suite_version"):
        load_suite()


def test_the_check_constants_hold_every_upper_case_constant_of_the_check_modules() -> None:
    constants = suite.check_constants()
    for name in ("DURATION_TOLERANCE", "HARD_SHARE", "TSS_PER_HOUR", "TOLERATED_ISSUES", "ZONE7"):
        assert f"checks.{name}" in constants
    assert "pick.TRACK_SEC" in constants
    assert "music.LOUDNESS" in constants
    assert constants["agent.songs.TOLERANCE"] == 0.10
    json.dumps(constants, allow_nan=False)


# --- the manifest ------------------------------------------------------------------------------


def test_the_suite_holds_every_smoke_and_song_case_once_in_its_group() -> None:
    index = MANIFEST.case_index()
    smoke = {c.name for c in load_cases()}
    assert smoke <= set(SUITE.design_cases)  # the 18
    assert set(SUITE.song_cases) - SUITE_OWN == {c.name for c in load_pick_cases()}  # the 3
    assert set(SUITE.design_cases) - smoke == SUITE_OWN & set(SUITE.design_cases)
    assert len(SUITE_OWN) == 19  # the adversarial, missing-music and cost cases (#209)
    assert len(SUITE.design_cases) == 18 + 12
    assert len(SUITE.song_cases) == 3 + 7
    assert len(index) == 21 + 19
    assert all(index[n][1] == "design" for n in SUITE.design_cases)
    assert all(index[n][1] == "songs" for n in SUITE.song_cases)
    assert set(MANIFEST.categories) == set(suite.CATEGORY_GROUPS)
    assert {n for n, (cat, _) in index.items() if cat == "adversarial"} == {
        "adversarial_brief", "adversarial_music", "revise_adversarial_music", "injected_title",
        "adv_brief_requests", "adv_malformed_hits", "adv_malformed_music", "adv_oversized_hits",
        "adv_oversized_music", "adv_pool_requests", "adv_tool_requests", "revise_adv_requests",
    }  # fmt: skip
    assert {n for n, (cat, _) in index.items() if cat == "revisions"} == {
        "revise_easier", "revise_more_sprints",
    }  # fmt: skip


def test_the_thresholds_are_the_adrs_defaults_and_the_ceilings_the_settings() -> None:
    r = MANIFEST.rules
    assert (r.repeat, r.pass_share, r.not_run_share_max) == (3, 0.9, 0.1)
    assert r.every_run_categories == ["adversarial"]
    assert r.every_run_checks == ["limits", "forbidden_text"]
    assert r.outcome_checks == {"limit_exceeded": "limits"}
    assert r.not_run_outcomes == ["model_unavailable", "outbound_blocked"]
    assert MANIFEST.groups["design"].median_cost_micros == 750_000
    assert MANIFEST.groups["songs"].median_cost_micros == 1_000_000
    fields = Settings.model_fields
    assert MANIFEST.groups["design"].job_ceiling_micros == fields["agent_job_cost_micros"].default
    assert MANIFEST.groups["songs"].job_ceiling_micros == fields["song_job_cost_micros"].default
    assert MANIFEST.zone7_tss_margin == 0.1


def _manifest(edit: Any) -> dict[str, Any]:
    data = copy.deepcopy(suite.read_manifest_data())
    edit(data)
    return data


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["categories"].update(extra={}),  # an unknown category
        lambda d: d["categories"].pop("cost"),  # a missing one
        lambda d: d["categories"]["song_choice"].update(design=["easy_30"]),  # wrong group
        lambda d: d["categories"]["standard"].update(songs=["x"]),
        lambda d: d["categories"]["cost"].update(design=["easy_30"]),  # a case twice
        lambda d: d["categories"]["cost"].update(design=["Bad Name"]),
        lambda d: d["groups"]["songs"].update(median_cost_micros=3_000_001),  # above the ceiling
        lambda d: d["groups"].pop("songs"),
        lambda d: d["rules"].update(every_run_categories=["nope"]),
        lambda d: d["rules"].update(not_run_outcomes=["submitted"]),
        lambda d: d["rules"].update(pass_share=0),
        lambda d: d["rules"].update(repeat=0),
        lambda d: d.update(suite_version=0),
        lambda d: d.update(digest="xyz"),
        lambda d: d.update(unknown=1),
    ],
)
def test_a_manifest_that_breaks_a_rule_is_refused(edit: Any) -> None:
    with pytest.raises(ValueError, match="validation error"):
        Manifest.model_validate(_manifest(edit))


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d["categories"]["cost"].update(design=["nowhere"]), "no case file"),
        (lambda d: d["categories"]["cost"].update(design=["pick_x"]), "no case file"),
        (
            lambda d: (
                d["categories"]["song_choice"]["songs"].remove("pick_30"),
                d["categories"]["cost"].update(design=["pick_30"]),
            ),
            "not a design case",
        ),
    ],
)
def test_the_suite_refuses_a_case_it_cannot_find_or_of_the_wrong_kind(
    monkeypatch: pytest.MonkeyPatch, edit: Any, message: str
) -> None:
    data = _manifest(edit)
    monkeypatch.setattr(suite, "read_manifest_data", lambda: data)
    with pytest.raises(SuiteError, match=message):
        load_suite()


# --- results format 2 --------------------------------------------------------------------------

START = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
HEADER_KEYS = {
    "results_format", "suite_version", "manifest_digest", "provider", "model", "effort",
    "prompt_versions", "classbeat_version", "mowl_version", "prices_version", "repeat",
    "started_at", "ended_at", "provider_kind", "results",
}  # fmt: skip


def header(**changes: Any) -> SuiteHeader:
    base = SuiteHeader(
        suite_version=MANIFEST.suite_version,
        manifest_digest=MANIFEST.digest,
        provider="anthropic",
        model="claude-opus-5-5",
        effort="medium",
        prompt_versions={"design": "a" * 64, "revise": "b" * 64},
        repeat=3,
        started_at=START,
        ended_at=START + timedelta(minutes=40),
        provider_kind="fake",
    )
    return dataclasses.replace(base, **changes)


def test_the_header_is_exactly_the_adrs() -> None:
    doc = suite_results(header(), [])
    assert set(doc) == HEADER_KEYS
    assert doc["results_format"] == 2
    assert doc["started_at"] == "2026-10-30T12:00:00+00:00"
    assert doc["ended_at"] == "2026-10-30T12:40:00+00:00"
    assert doc["prices_version"]
    assert doc["mowl_version"]
    assert doc["classbeat_version"]
    assert doc["provider_kind"] == "fake"
    assert doc["provider"] == "anthropic"
    with pytest.raises(ValueError, match="timezone-aware"):
        suite_results(header(started_at=datetime(2026, 10, 30)), [])


async def _real_rows(repeat: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, case in sorted(SUITE.design_cases.items()):
        result = await run_case(
            case,
            (adv_provider if name in SUITE_OWN else design_provider)(case),
            CONFIG,
            repeat=repeat,
            zone7_tss_margin=MANIFEST.zone7_tss_margin,
        )
        rows.append(suite_row(result, SUITE.category_of(name)))
    for name, case in sorted(SUITE.song_cases.items()):
        result, _, _ = await run_pick_case(
            case,
            (adv_provider if name in SUITE_OWN else pick_provider)(case),
            CONFIG,
            (POOL_LIMITS, CHOOSE_LIMITS),
        )
        rows.append(
            song_suite_row(
                result, SUITE.category_of(name), repeat=repeat, model=CONFIG.model,
                effort=CONFIG.effort,
            )
        )  # fmt: skip
    return rows


async def test_results_hold_numbers_and_names_only_for_design_and_song_runs() -> None:
    rows = await _real_rows()
    body = suite_results_json(header(), rows)
    for planted in (
        "PWNED", "admin mode", "Ignore all previous", "Smoke Song", "Smoke Artist", "SmokeSynth",
        "SmokePick", "SmokeKept", "Kept Song", "Invented Album", "spotify:",
    ):  # fmt: skip
        assert planted.lower() not in body.lower(), planted
    data = json.loads(body)
    by_case = {r["case"]: r for r in data["results"]}
    assert by_case["easy_30"]["category"] == "standard"
    assert by_case["injected_title"]["category"] == "adversarial"
    assert set(by_case["easy_30"]) == {
        "case", "repeat", "model", "effort", "model_answered", "prompt_version", "passed",
        "outcome", "turns", "tool_calls", "corrections", "cost_micros", "wall_sec",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "duration_sec", "tss", "tss_bounds", "changes_on_music", "checks", "timeline",
        "category", "tss_approximate", "scores",
    }  # fmt: skip
    assert set(by_case["pick_30"]) == {
        "case", "repeat", "category", "model", "effort", "prompt_versions", "passed", "outcome",
        "turns", "tool_calls", "cost_micros", "checks", "scores",
    }  # fmt: skip
    assert set(by_case["pick_30"]["prompt_versions"]) == {"pick_pool", "choose_songs"}


async def test_the_recorded_smoke_runs_pass_the_gate_in_both_groups() -> None:
    rows = [row for repeat in range(3) for row in await _real_rows(repeat)]
    verdict = gate.evaluate(suite_results(header(), rows), MANIFEST)
    assert set(verdict.groups) == {"design", "songs"}
    for group in verdict.groups.values():
        assert group.status == "passed", group.as_json()
        assert group.runs == (90 if group.group == "design" else 30)


# --- zone 7 (section 7) ------------------------------------------------------------------------


def _outcome() -> StageOutcome:
    return StageOutcome(
        None, uuid4(), RunInfo("design", "fake", "m", "e", "0" * 64), 3, 3, Usage(), 100, "m"
    )


def test_the_zone7_margin_widens_only_a_zone7_class_upper_tss_bound() -> None:
    case = SUITE.design_cases["default_30"]
    ctx = DesignContext.build(case.brief, build_music(case.music))
    result = check(WorkoutDraft.model_validate(good_draft(case)), ctx.map)
    low, high = tss_bounds_for(case, result.duration_sec)
    assert tss_bounds_for(case, result.duration_sec, zone7=True, zone7_margin=0.1) == (
        low,
        pytest.approx(high * 1.1),
    )
    assert tss_bounds_for(case, result.duration_sec, zone7=False, zone7_margin=0.1) == (low, high)
    spec = json.loads(json.dumps(result.spec))
    spec["segments"][1]["intervals"][0]["zone"] = 7
    over = high * 1.05

    def tss_ok(spec: dict[str, Any], margin: float) -> bool:
        res = dataclasses.replace(result, spec=spec, tss=over)
        found = run_checks(
            case, res, _outcome(), Limits(), float(ctx.map["duration"]), zone7_tss_margin=margin
        )
        return {c.name: c.ok for c in found}["tss"]

    assert tss_ok(spec, 0.1)  # zone 7 and the margin: inside
    assert not tss_ok(spec, 0.0)  # the smoke set: no margin
    assert not tss_ok(result.spec, 0.1)  # no zone 7: no margin
