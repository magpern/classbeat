"""The ``class_patterns`` suite category (ADR 0017 section 5, issue #399). Fixtures only: invented
briefs and scripted ``FakeProvider`` runs on synthetic music, no model, no network. Every case
passes against its recorded script; each predicate also has a failing script (a draft that
ignores the pattern, or one that "corrects" the draft away from the brief)."""

import copy
import json
from typing import Any

import pytest

from agentfake import CONFIG
from classbeat.agent import DesignContext
from classbeat.agent.design import check
from classbeat.agent.schemas import Brief, WorkoutDraft
from classbeat.design.guidance import rule_set_for
from classbeat.design.rules import default_registry, evaluate
from classbeat.design.selection import select_profile_for_brief
from classbeat.evals.cases import Case, PatternExpect
from classbeat.evals.music import build_music
from classbeat.evals.runner import CaseResult, run_case
from classbeat.evals.suite import CATEGORY_GROUPS, load_suite
from patternscripts import FOLLOWS, corrected_draft, pattern_draft
from smokedrafts import iv, seg
from suitescripts import provider_for_case

SUITE = load_suite()
CATEGORY = "class_patterns"
CASES = [SUITE.design_cases[n] for n in SUITE.manifest.cases_of(CATEGORY, "design")]
FOLLOWING = [c for c in CASES if c.expect.patterns and c.expect.patterns.mode == "follows"]
DEVIATING = [c for c in CASES if c.expect.patterns and c.expect.patterns.mode == "deviates"]
MARGIN = SUITE.manifest.zone7_tss_margin


async def run(case: Case, draft: dict[str, Any] | None = None) -> CaseResult:
    return await run_case(case, provider_for_case(case, draft), CONFIG, zone7_tss_margin=MARGIN)


def ok_of(result: CaseResult) -> dict[str, bool]:
    return {c.name: c.ok for c in result.checks}


def fitted(case: Case, draft: dict[str, Any]) -> dict[str, Any]:
    ctx = DesignContext.build(case.brief, build_music(case.music))
    return check(WorkoutDraft.model_validate(draft), ctx.map).spec


# --- the category -------------------------------------------------------------------------------


def test_the_category_is_in_the_manifest_for_the_design_group_only() -> None:
    assert CATEGORY_GROUPS[CATEGORY] == frozenset({"design"})
    assert SUITE.manifest.cases_of(CATEGORY, "songs") == []
    assert len(CASES) == 10
    assert SUITE.manifest.rules.pass_share == 0.9  # the ordinary category rule, nothing special
    assert CATEGORY not in SUITE.manifest.rules.every_run_categories


def test_a_follows_case_for_every_shipped_profile_and_every_kind_of_ask() -> None:
    shipped = set(default_registry().profiles)
    chosen = {p.id for c in FOLLOWING if (p := select_profile_for_brief(c.brief))}
    assert chosen == shipped
    assert {c.name for c in FOLLOWING} == set(FOLLOWS)
    asked = {
        field
        for c in DEVIATING
        for field in ("hard_start_by_sec", "warmup_max_sec", "sprints_min", "max_zone")
        if getattr(c.expect.patterns, field) is not None
    }
    assert asked == {"hard_start_by_sec", "warmup_max_sec", "sprints_min", "max_zone"}


def test_the_cases_are_invented_and_a_follows_brief_asks_for_nothing() -> None:
    for case in CASES:
        assert not case.music.titles
        assert not case.music.artists
        assert case.music.name == "Invented Smoke Mix"
        assert case.brief.class_type is not None
        assert case.revise is None
    for case in FOLLOWING:
        b = case.brief
        assert (b.intensity, b.text, b.style, b.sprints, b.races, b.climbs, b.ftp_test) == (
            "moderate", "", "", 0, 0, 0, None,
        )  # fmt: skip


def test_the_pattern_expectation_is_consistent() -> None:
    with pytest.raises(ValueError, match="deviates"):
        PatternExpect(mode="deviates")
    with pytest.raises(ValueError, match="deviates"):
        PatternExpect(mode="follows", sprints_min=3)
    base = FOLLOWING[0].model_dump(mode="json")
    for change in ({"text": "Hard."}, {"sprints": 2}, {"intensity": "hard"}, {"class_type": None}):
        bad = copy.deepcopy(base)
        bad["brief"].update(change)
        with pytest.raises(ValueError, match="follows case"):
            Case.model_validate_json(json.dumps(bad))
    revise = copy.deepcopy(base)
    revise["revise"] = {"request": "More.", "base": pattern_draft(FOLLOWING[0])}
    with pytest.raises(ValueError, match="design case"):
        Case.model_validate_json(json.dumps(revise))


# --- following the pattern ----------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
async def test_the_case_passes_against_its_recorded_script(case: Case) -> None:
    result = await run(case)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert result.cost_micros <= SUITE.manifest.groups["design"].median_cost_micros
    names = set(ok_of(result))
    assert ("pattern_followed" in names) == (case in FOLLOWING)
    assert ({"asked_present", "hits_explained"} <= names) == (case in DEVIATING)


@pytest.mark.parametrize("case", FOLLOWING, ids=lambda c: c.name)
def test_a_followed_pattern_leaves_no_hit_at_all(case: Case) -> None:
    profile = select_profile_for_brief(case.brief)
    assert profile is not None
    assert evaluate(fitted(case, pattern_draft(case)), case.brief, profile) == []


def with_short_warmup(draft: dict[str, Any], to: int, give_to: tuple[int, int]) -> dict[str, Any]:
    """The warm-up cut to ``to`` seconds; the rest goes to interval ``give_to`` (segment,
    interval) so the length stays."""
    out = copy.deepcopy(draft)
    warm = out["segments"][0]["intervals"]
    cut = warm[0]["sec"] + warm[1]["sec"] - to
    warm[0]["sec"] -= cut // 2
    warm[1]["sec"] -= cut - cut // 2
    out["segments"][give_to[0]]["intervals"][give_to[1]]["sec"] += cut
    return out


def hard_early(draft: dict[str, Any]) -> dict[str, Any]:
    """The warm-up cut to two minutes and a zone 4 interval of the rest right after it."""
    out = with_short_warmup(draft, 120, (1, 0))
    first = out["segments"][1]["intervals"]
    cut = first[0]["sec"] - draft["segments"][1]["intervals"][0]["sec"]
    first[0]["sec"] -= cut
    first.insert(0, iv(cut, 4, [85, 95]))
    return out


@pytest.mark.parametrize("case", FOLLOWING, ids=lambda c: c.name)
async def test_a_draft_that_breaks_the_pattern_fails_the_case_but_is_submitted(case: Case) -> None:
    draft = hard_early(pattern_draft(case))
    result = await run(case, draft)
    assert result.outcome == "submitted"  # a rule hit is a note, never a refusal
    assert ok_of(result)["pattern_followed"] is False
    assert not result.passed


async def test_the_hit_detail_names_rules_and_numbers_only() -> None:
    case = FOLLOWING[0]
    result = await run(case, hard_early(pattern_draft(case)))
    detail = next(c.detail for c in result.checks if c.name == "pattern_followed")
    assert detail.startswith("3 warn hits: ['warmup_short'")
    assert "Invented" not in detail


# --- deviating when the brief asks (the brief wins) ---------------------------------------------


@pytest.mark.parametrize("case", DEVIATING, ids=lambda c: c.name)
def test_the_asked_draft_does_hit_rules_and_every_hit_is_explained(case: Case) -> None:
    profile = select_profile_for_brief(case.brief)
    assert profile is not None
    hits = evaluate(fitted(case, pattern_draft(case)), case.brief, profile)
    assert hits, "a deviation the rules did not notice would prove nothing"
    assert all(h.explained_by_brief and h.severity == "info" for h in hits)


@pytest.mark.parametrize("case", DEVIATING, ids=lambda c: c.name)
async def test_a_model_that_corrects_the_draft_away_from_the_brief_fails(case: Case) -> None:
    result = await run(case, corrected_draft(case))
    assert result.outcome == "submitted"
    assert ok_of(result)["asked_present"] is False
    assert not result.passed


async def test_a_hit_the_brief_does_not_explain_fails_the_deviation() -> None:
    # Ten sprints are asked; a short warm-up is not, so warmup_short stays unexplained.
    case = next(c for c in DEVIATING if c.name == "cp_ten_sprints_30")
    draft = with_short_warmup(pattern_draft(case), 100, (-1, 0))
    result = await run(case, draft)
    flags = ok_of(result)
    assert flags["asked_present"] is True
    assert flags["hits_explained"] is False
    detail = next(c.detail for c in result.checks if c.name == "hits_explained")
    assert "warmup_short" in detail


@pytest.mark.parametrize(
    ("name", "ask", "stricter"),
    [
        ("cp_hard_start_30", "hard_start_by_sec", 0),
        ("cp_short_warmup_45", "warmup_max_sec", 60),
        ("cp_ten_sprints_30", "sprints_min", 50),
        ("cp_easy_spin_45", "max_zone", 1),
    ],
)
async def test_each_asked_feature_is_judged_on_its_own(name: str, ask: str, stricter: int) -> None:
    case = next(c for c in DEVIATING if c.name == name).model_copy(deep=True)
    assert case.expect.patterns is not None
    assert getattr(case.expect.patterns, ask) is not None
    setattr(case.expect.patterns, ask, stricter)
    result = await run(case, pattern_draft(case))
    assert ok_of(result)["asked_present"] is False


# --- never blocks, and the rules are what production gives the model ----------------------------


async def test_a_draft_that_breaks_many_rules_is_accepted() -> None:
    case = FOLLOWING[0]
    segments = [
        seg("Warm-up", "warmup", iv(60, 1, [70, 80]), iv(60, 5, [100, 110])),
        seg("All out", "main", iv(1530, 5, [100, 110], standing=True)),
        seg("Cooldown", "cooldown", iv(150, 1, [60, 70])),
    ]
    draft = {"name": "Invented extreme", "description": "Breaks the rules.", "segments": segments}
    profile = select_profile_for_brief(case.brief)
    assert profile is not None
    hits = evaluate(fitted(case, draft), case.brief, profile)
    assert len(hits) == 5  # the cap; more rules were violated than are shown
    assert all(h.severity == "warn" for h in hits)
    result = await run(case, draft)
    assert result.outcome == "submitted"  # accepted by submit_draft, never a ToolError
    assert ok_of(result)["pattern_followed"] is False


def test_the_run_gets_the_rules_a_production_job_builds() -> None:
    for case in CASES:
        rules = rule_set_for(case.brief)
        assert rules is not None, case.name
        assert rules.section()
    assert rule_set_for(Brief(length_min=15)) is None  # no profile within 3 minutes: no rules
