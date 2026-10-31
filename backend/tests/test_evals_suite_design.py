"""The design-group suite cases (ADR 0012 section 4; issue #206): ``standard``, ``music_sync``,
``games_ftp`` and ``revisions``. Every case passes against its recorded ``FakeProvider`` script
(``suitescripts``); each music-sync check and revision predicate also has a failing script, a
careless model's draft that breaks that rule. No model, no network, synthetic music only."""

import copy
from types import SimpleNamespace
from typing import Any

import pytest
from mowl import workout

from agentfake import CONFIG
from classbeat.evals import checks
from classbeat.evals.cases import Case, MusicSyncExpect
from classbeat.evals.runner import CaseResult, run_case
from classbeat.evals.suite import load_suite
from smokedrafts import make_draft
from suitescripts import (
    broken_phrase,
    broken_sprint,
    fits,
    good_draft,
    move_climb,
    music_map,
    provider_for_case,
    script_for,
)

SUITE = load_suite()
INDEX = SUITE.manifest.case_index()
CATEGORIES = ("standard", "music_sync", "games_ftp", "revisions")
SMOKE = {
    "climbs_45", "default_30", "easy_30", "hard_45_sprints", "long_60", "long_text_30",
    "minimum_10", "ftp_20_60", "ftp_5_45", "races_40", "revise_easier", "revise_more_sprints",
}  # fmt: skip
OWN: dict[str, list[Case]] = {
    cat: [SUITE.design_cases[n] for n in SUITE.manifest.cases_of(cat, "design") if n not in SMOKE]
    for cat in CATEGORIES
}
ALL = [c for cat in CATEGORIES for c in OWN[cat]]
BY_NAME = {c.name: c for c in ALL}
# The checks each category's cases add to the smoke set's.
ADDED = {
    "standard": set[str](),
    "music_sync": {"sprints_on_drops", "phrase_changes"},
    "games_ftp": set[str](),
    "revisions": {"revision", "kept_cooldown"},
}


async def run(case: Case, draft: dict[str, Any] | None = None) -> CaseResult:
    return await run_case(
        case,
        provider_for_case(case, draft),
        CONFIG,
        zone7_tss_margin=SUITE.manifest.zone7_tss_margin,
    )


def ok_of(result: CaseResult) -> dict[str, bool]:
    return {c.name: c.ok for c in result.checks}


# --- the cases ----------------------------------------------------------------------------------


def test_each_category_has_at_least_six_new_cases_on_synthetic_music() -> None:
    for cat in CATEGORIES:
        assert len(OWN[cat]) >= 6, cat
        assert all(INDEX[c.name] == (cat, "design") for c in OWN[cat])
    for case in ALL:
        assert not case.music.titles
        assert not case.music.artists
        assert case.music.name == "Invented Smoke Mix"


def test_the_cases_cover_lengths_intensities_games_ftp_tests_and_revision_kinds() -> None:
    lengths = [c.brief.length_min for c in ALL]
    assert min(lengths) <= 15
    assert max(lengths) == 90
    for cat in ("standard", "music_sync", "revisions"):
        assert {c.brief.intensity for c in OWN[cat]} == {"easy", "moderate", "hard"}, cat
    games = {g for c in OWN["games_ftp"] for g in c.expect.games}
    assert games == set(workout.GAMES)
    assert {c.brief.ftp_test for c in OWN["games_ftp"]} >= {3, 5, 20}
    kinds = {c.expect.revision.kind for c in OWN["revisions"] if c.expect.revision}
    assert kinds == {"more_sprints", "easier", "shorter", "climb_moved"}
    assert all(c.revise is not None and c.expect.revision for c in OWN["revisions"])
    assert all(c.expect.music_sync is not None for c in OWN["music_sync"])
    assert sum(c.brief.sprints + c.brief.races > 0 for c in OWN["music_sync"]) >= 5


@pytest.mark.parametrize("case", ALL, ids=lambda c: c.name)
async def test_the_case_passes_against_its_recorded_script(case: Case) -> None:
    fake = provider_for_case(case)
    result = await run_case(case, fake, CONFIG, zone7_tss_margin=SUITE.manifest.zone7_tss_margin)
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert fake.remaining == 0
    assert result.turns == len(script_for(case).turns)
    names = set(ok_of(result))
    assert ADDED[INDEX[case.name][0]] <= names
    if case.expect.games:
        assert "games" in names
    if case.expect.ftp_warmup_min_sec:
        assert "ftp_warmup" in names
    assert result.cost_micros <= SUITE.manifest.groups["design"].median_cost_micros


# --- music sync: a sprint on a drop, no zone change mid-phrase ----------------------------------

SPRINTING = [c for c in OWN["music_sync"] if c.brief.sprints + c.brief.races]


@pytest.mark.parametrize("case", SPRINTING, ids=lambda c: c.name)
async def test_a_sprint_off_its_drop_fails(case: Case) -> None:
    result = await run(case, broken_sprint(good_draft(case)))
    assert result.outcome == "submitted"
    assert ok_of(result)["sprints_on_drops"] is False
    assert not result.passed


@pytest.mark.parametrize("case", OWN["music_sync"], ids=lambda c: c.name)
async def test_a_zone_change_off_its_phrase_fails(case: Case) -> None:
    result = await run(case, broken_phrase(case, good_draft(case)))
    assert result.outcome == "submitted"
    ok = ok_of(result)
    assert ok["phrase_changes"] is False
    assert ok["sprints_on_drops"] is True  # only the phrase rule is broken
    assert not result.passed


async def test_the_case_can_allow_a_change_off_the_phrase() -> None:
    case = BY_NAME["sync_easy_30"]
    lenient = case.model_copy(
        update={
            "expect": case.expect.model_copy(
                update={"music_sync": MusicSyncExpect(max_off_phrase=1)}
            )
        }
    )
    result = await run(lenient, broken_phrase(case, good_draft(case)))
    assert ok_of(result)["phrase_changes"] is True


def test_music_without_a_drop_has_no_sprint_to_place() -> None:
    case = SUITE.design_cases["music_no_sections"]
    m = music_map(case)
    assert not [e for e in m["events"] if e["kind"] == "drop"]
    spec = {"segments": [{"name": "S", "kind": "main", "intervals": [{"sec": 30, "zone": 6}]}]}
    found = checks.sprints_on_drops(spec, m)
    assert found.ok
    assert "no drop" in found.detail


def test_the_music_sync_checks_need_the_map() -> None:
    case = BY_NAME["sync_easy_30"]
    with pytest.raises(ValueError, match="music map"):
        checks.suite_checks(case, SimpleNamespace(spec={"segments": []}), None)  # pyright: ignore[reportArgumentType]


# --- games and FTP tests --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "games"),
    [("games_racetrack_45", ()), ("games_racetrack_45", ("racetrack", "overflow"))],
    ids=["missing", "extra"],
)
async def test_a_missing_or_extra_game_fails(name: str, games: tuple[str, ...]) -> None:
    case = BY_NAME[name]
    b = case.brief
    draft = make_draft(b.length_min * 60, intensity=b.intensity, climbs=b.climbs, games=games)
    result = await run(case, draft)
    assert result.outcome == "submitted"
    assert ok_of(result)["games"] is False


async def test_an_ftp_test_without_its_warm_up_fails() -> None:
    case = BY_NAME["ftp_3_30"]
    b = case.brief
    draft = next(
        d
        for lead in range(60, 300, 10)
        if fits(
            case, d := make_draft(b.length_min * 60, intensity=b.intensity, ftp=3, ftp_lead=lead)
        )
    )
    result = await run(case, draft)
    assert result.outcome == "submitted"
    assert ok_of(result)["ftp_warmup"] is False


# --- revisions ---------------------------------------------------------------------------------


def _longer_cooldown(draft: dict[str, Any], by: int) -> dict[str, Any]:
    out = copy.deepcopy(draft)
    out["segments"][-1]["intervals"][0]["sec"] += by
    out["segments"][-2]["intervals"][0]["sec"] -= by  # the same length
    return out


FAILING: list[tuple[str, Any, str]] = [
    (
        "rev_more_sprints_30",
        lambda: make_draft(1800, intensity="moderate", climbs=1, sprints=2),  # one more, not two
        "revision",
    ),
    (
        "rev_more_sprints_60",
        lambda: make_draft(3600, intensity="hard", climbs=0, sprints=3, races=1),  # climb gone
        "kept_efforts",
    ),
    (
        "rev_more_sprints_30",
        lambda: make_draft(1500, intensity="moderate", climbs=1, sprints=3),  # five minutes cut
        "kept_length",
    ),
    (
        "rev_easier_40",
        lambda: make_draft(2400, intensity="hard", climbs=1, sprints=2),  # as hard as before
        "revision",
    ),
    (
        "rev_easier_50_to_easy",
        lambda: _longer_cooldown(make_draft(3000, intensity="easy", climbs=1), 150),
        "kept_cooldown",
    ),
    (
        "rev_shorter_45_to_30",
        lambda: make_draft(2400, intensity="moderate", climbs=1, sprints=1),  # 40 minutes
        "revision",
    ),
    (
        "rev_climb_to_end_45",
        lambda: make_draft(2700, intensity="moderate", climbs=1, sprints=3),  # the climb stays
        "revision",
    ),
    (
        "rev_climb_to_start_40",
        lambda: move_climb(make_draft(2400, intensity="moderate", climbs=1, sprints=2), "end"),
        "revision",
    ),
    (
        "rev_climb_to_end_45",
        lambda: move_climb(make_draft(2700, intensity="moderate", climbs=1, sprints=1), "end"),
        "kept_efforts",  # a sprint lost on the way
    ),
]


@pytest.mark.parametrize(
    ("name", "draft", "check"), FAILING, ids=[f"{n}-{c}" for n, _, c in FAILING]
)
async def test_a_revision_that_misses_its_request_or_its_bounds_fails(
    name: str, draft: Any, check: str
) -> None:
    case = BY_NAME[name]
    result = await run(case, draft())
    assert result.outcome == "submitted"
    assert ok_of(result)[check] is False, [c.detail for c in result.checks]
    assert not result.passed


async def test_a_shorter_revision_is_measured_against_the_asked_length() -> None:
    result = await run(BY_NAME["rev_shorter_45_to_30"])
    ok = ok_of(result)
    assert ok["duration"]
    assert ok["revision"]
    assert "kept_length" not in ok  # the length is what changed
    assert "kept_efforts" not in ok
