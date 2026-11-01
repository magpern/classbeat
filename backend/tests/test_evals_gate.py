"""The suite's verdict (ADR 0012 section 4.3; issue #202): ``evaluate`` on hand-built result
lists. Each rule has a passing list mutated to break exactly that rule, and the boundaries where
the rules turn. The stored verdict (``GroupVerdict.as_json``) is checked against the rule of the
``model_eval_results`` CHECK (section 5, #211)."""

import copy
import json
from typing import Any

import pytest

from classbeat.evals.gate import GroupVerdict, ResultsInvalid, Verdict, evaluate
from classbeat.evals.suite import Manifest

DIGEST = "d" * 64
STANDARD = ["s_a", "s_b", "s_c", "s_d"]
ADV_DESIGN = ["adv_d"]
ADV_SONGS = ["adv_s"]
SONG_CHOICE = ["pick_a"]
CLASS_PATTERNS = ["cp_a"]

MANIFEST = Manifest.model_validate(
    {
        "suite_version": 4,
        "digest": DIGEST,
        "rules": {
            "repeat": 3,
            "pass_share": 0.9,
            "every_run_categories": ["adversarial"],
            "every_run_checks": ["limits", "forbidden_text"],
            "outcome_checks": {"limit_exceeded": "limits"},
            "not_run_outcomes": ["model_unavailable", "outbound_blocked"],
            "not_run_share_max": 0.1,
        },
        "groups": {
            "design": {"median_cost_micros": 750_000, "job_ceiling_micros": 1_500_000},
            "songs": {"median_cost_micros": 1_000_000, "job_ceiling_micros": 3_000_000},
        },
        "zone7_tss_margin": 0.1,
        "categories": {
            "standard": {"design": STANDARD},
            "music_sync": {},
            "games_ftp": {},
            "revisions": {},
            "song_choice": {"songs": SONG_CHOICE},
            "missing_music": {},
            "class_patterns": {"design": CLASS_PATTERNS},
            "adversarial": {"design": ADV_DESIGN, "songs": ADV_SONGS},
            "cost": {},
        },
    }
)
CATEGORY = {n: c for n, (c, _) in MANIFEST.case_index().items()}


def run(
    case: str,
    repeat: int,
    *,
    outcome: str = "submitted",
    cost: int = 100_000,
    failed: tuple[str, ...] = (),
    **extra: Any,
) -> dict[str, Any]:
    names = ("structure", "limits", "forbidden_text") if outcome == "submitted" else ()
    checks = [{"name": n, "ok": n not in failed, "detail": "numbers"} for n in names]
    row = {
        "case": case,
        "repeat": repeat,
        "category": CATEGORY.get(case, "standard"),
        "outcome": outcome,
        "passed": outcome == "submitted" and not failed,
        "cost_micros": cost,
        "checks": checks,
        "turns": 3,
    }
    return {**row, **extra}


def rows(repeats: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Every case of the manifest passing ``repeats[case]`` (default 3) times."""
    counts = repeats or {}
    return [run(case, r) for case in CATEGORY for r in range(counts.get(case, 3))]


def doc(results: list[dict[str, Any]], **header: Any) -> dict[str, Any]:
    return {
        "results_format": 2,
        "suite_version": 4,
        "manifest_digest": DIGEST,
        "provider": "anthropic",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "results": results,
        **header,
    }


def replace(results: list[dict[str, Any]], case: str, repeat: int, **changes: Any) -> None:
    for i, row in enumerate(results):
        if row["case"] == case and row["repeat"] == repeat:
            results[i] = run(case, repeat, **changes)
            return
    raise AssertionError(f"no run {case} {repeat}")


def verdict(results: list[dict[str, Any]], **header: Any) -> Verdict:
    return evaluate(doc(results, **header), MANIFEST)


def design(results: list[dict[str, Any]], **header: Any) -> GroupVerdict:
    return verdict(results, **header).groups["design"]


def songs(results: list[dict[str, Any]], **header: Any) -> GroupVerdict:
    return verdict(results, **header).groups["songs"]


def std_rules(g: GroupVerdict) -> tuple[str, ...]:
    return g.categories["standard"].rules_failed


# --- the passing list --------------------------------------------------------------------------


def test_a_complete_passing_list_passes_both_groups() -> None:
    v = verdict(rows())
    assert set(v.groups) == {"design", "songs"}
    assert (v.suite_version, v.manifest_digest) == (4, DIGEST)
    d, s = v.groups["design"], v.groups["songs"]
    assert (d.status, d.runs, d.passes, d.not_run, d.median_cost_micros) == (
        "passed", 18, 18, 0, 100_000,
    )  # fmt: skip
    assert (s.status, s.runs, s.passes) == ("passed", 6, 6)
    assert set(d.categories) == {"standard", "class_patterns", "adversarial"}
    assert set(s.categories) == {"song_choice", "adversarial"}
    assert s.categories["adversarial"].runs == 3  # the songs group's adversarial cases only


def test_the_class_patterns_category_is_part_of_the_verdict() -> None:
    results = rows()
    assert design(results).categories["class_patterns"].runs == 3
    for r in range(3):
        replace(results, "cp_a", r, failed=("structure",))
    g = design(results)  # no run of the category passes
    assert g.status == "failed"
    assert g.categories["class_patterns"].rules_failed == ("pass_share", "each_case_once")
    assert g.categories["standard"].rules_failed == ()
    assert not g.as_json()["passed"]


def test_the_verdict_is_per_stage_group() -> None:
    results = rows()
    for r in range(3):
        replace(results, "pick_a", r, failed=("structure",))
    v = verdict(results)
    assert v.groups["design"].passed
    assert not v.groups["songs"].passed
    assert v.groups["songs"].categories["song_choice"].rules_failed == (
        "pass_share", "each_case_once",
    )  # fmt: skip


def test_a_group_without_runs_in_the_file_gets_no_verdict() -> None:
    v = verdict(
        [r for r in rows() if CATEGORY[r["case"]] == "standard" or r["case"] in ("adv_d", "cp_a")]
    )
    assert set(v.groups) == {"design"}
    assert v.groups["design"].passed


# --- repeats -----------------------------------------------------------------------------------


def test_a_case_with_fewer_runs_than_the_repeat_count_fails() -> None:
    g = design(rows({"s_b": 2}))
    assert g.status == "failed"
    assert std_rules(g) == ("repeats",)


def test_a_case_missing_from_the_file_fails_repeats() -> None:
    g = design(rows({"s_c": 0}))
    assert std_rules(g) == ("repeats",)


def test_repeats_counts_distinct_runs_and_an_outage_is_an_attempted_run() -> None:
    results = rows()
    replace(results, "s_a", 2, outcome="model_unavailable")
    assert design(results).status == "passed"


# --- every run: adversarial, limits, forbidden_text --------------------------------------------


@pytest.mark.parametrize(("case", "group"), [("adv_d", "design"), ("adv_s", "songs")])
def test_one_failed_adversarial_run_fails_its_group(case: str, group: str) -> None:
    results = rows()
    replace(results, case, 1, failed=("structure",))
    g = verdict(results).groups[group]  # pyright: ignore[reportArgumentType]
    assert g.status == "failed"
    assert g.categories["adversarial"].rules_failed == ("every_run",)


def test_an_adversarial_run_that_did_not_submit_fails_every_run() -> None:
    results = rows()
    replace(results, "adv_d", 0, outcome="invalid_output")
    assert design(results).categories["adversarial"].rules_failed == ("every_run",)


def test_one_other_failed_run_within_the_share_still_passes() -> None:
    results = rows()  # standard: 12 runs, 11 passing is above 90 %
    replace(results, "s_a", 0, failed=("structure",))
    g = design(results)
    assert g.status == "passed"
    assert g.categories["standard"].passes == 11


@pytest.mark.parametrize("check", ["limits", "forbidden_text"])
def test_one_failed_limits_or_forbidden_text_check_fails_any_category(check: str) -> None:
    results = rows()
    replace(results, "s_a", 0, failed=(check,))  # 11 of 12: the share alone would pass
    g = design(results)
    assert g.status == "failed"
    assert std_rules(g) == (check,)


@pytest.mark.parametrize("check", ["limits", "forbidden_text"])
def test_a_submitted_run_without_a_required_check_failed_it(check: str) -> None:
    results = rows()
    results[0]["checks"] = [c for c in results[0]["checks"] if c["name"] != check]
    assert results[0]["case"] == "s_a"  # 11 of 12 standard runs still pass: the share holds
    g = design(results)
    assert g.status == "failed"
    assert std_rules(g) == (check,)


def test_a_run_that_did_not_submit_is_not_missing_its_checks() -> None:
    results = rows()
    replace(results, "s_a", 0, outcome="invalid_output")  # no checks at all
    g = design(results)
    assert g.status == "passed"
    assert std_rules(g) == ()


def test_a_run_stopped_by_a_limit_failed_the_limits_check() -> None:
    results = rows()
    replace(results, "s_a", 0, outcome="limit_exceeded")
    assert std_rules(design(results)) == ("limits",)
    results = rows()
    replace(results, "pick_a", 0, outcome="limit_exceeded")  # a song run has no limits check
    assert songs(results).categories["song_choice"].rules_failed == ("pass_share", "limits")


# --- 90 % and each case once -------------------------------------------------------------------


def test_exactly_ninety_percent_passes_and_less_fails() -> None:
    results = rows(dict.fromkeys(STANDARD, 5))  # 20 standard runs
    replace(results, "s_a", 0, failed=("structure",))
    replace(results, "s_b", 0, failed=("structure",))
    assert design(results).status == "passed"  # 18 of 20
    replace(results, "s_c", 0, failed=("structure",))
    g = design(results)  # 17 of 20
    assert g.status == "failed"
    assert std_rules(g) == ("pass_share",)


def test_a_failed_run_that_did_not_submit_counts_against_the_share() -> None:
    results = rows()
    replace(results, "s_a", 0, outcome="invalid_output")
    replace(results, "s_b", 0, outcome="model_refused")
    assert std_rules(design(results)) == ("pass_share",)


def test_a_case_that_never_passes_fails_even_within_the_share() -> None:
    results = rows({"s_b": 10, "s_c": 10, "s_d": 10})  # 33 standard runs
    for r in range(3):
        replace(results, "s_a", r, failed=("structure",))
    g = design(results)  # 30 of 33 pass: above 90 %
    assert g.status == "failed"
    assert std_rules(g) == ("each_case_once",)


# --- cost ----------------------------------------------------------------------------------------


def _costs(results: list[dict[str, Any]], group_cases: list[str], cost: int) -> None:
    for row in results:
        if row["case"] in group_cases:
            row["cost_micros"] = cost


def test_the_median_cost_per_group_at_most_its_threshold() -> None:
    results = rows()
    _costs(results, [*STANDARD, *ADV_DESIGN], 750_000)
    _costs(results, [*SONG_CHOICE, *ADV_SONGS], 1_000_000)
    v = verdict(results)
    assert v.groups["design"].passed
    assert v.groups["songs"].passed
    _costs(results, [*STANDARD, *ADV_DESIGN], 750_001)
    v = verdict(results)
    assert v.groups["design"].rules_failed == ("median_cost",)
    assert v.groups["songs"].passed  # each group has its own median and threshold
    _costs(results, [*SONG_CHOICE, *ADV_SONGS], 1_000_001)
    assert songs(results).rules_failed == ("median_cost",)


def test_the_median_of_an_even_count_is_the_mean_of_the_middle_two() -> None:
    results = rows()  # songs: 6 runs
    for row in results:
        if row["case"] in (*SONG_CHOICE, *ADV_SONGS):
            row["cost_micros"] = 900_000 if row["repeat"] else 1_100_001
    g = songs(results)  # sorted: 900k x4, 1.1M x2: the median is 900k
    assert g.passed
    assert g.median_cost_micros == 900_000
    for row in results:
        if row["case"] in (*SONG_CHOICE, *ADV_SONGS):
            row["cost_micros"] = 900_000 if row["case"] in ADV_SONGS else 1_100_000
    g = songs(results)  # 900k x3, 1.1M x3: (900k + 1.1M) / 2 is exactly the threshold
    assert g.passed
    assert g.median_cost_micros == 1_000_000
    _costs(results, SONG_CHOICE, 1_100_001)
    g = songs(results)  # 1_000_000.5
    assert g.rules_failed == ("median_cost",)
    assert g.median_cost_micros == 1_000_001  # rounded up


def test_one_run_over_its_job_ceiling_fails_the_group() -> None:
    results = rows()
    replace(results, "s_a", 0, cost=1_500_001)
    g = design(results)
    assert g.rules_failed == ("job_ceiling",)
    results = rows()
    replace(results, "s_a", 0, cost=1_500_000)
    assert design(results).passed


def test_the_median_counts_only_runs_that_ran() -> None:
    results = rows({**dict.fromkeys(STANDARD, 10), "cp_a": 0})  # design: 43 runs
    mine = [row for row in results if row["case"] in (*STANDARD, *ADV_DESIGN)]
    for n, row in enumerate(mine):
        if n < 4:  # 4 outages that cost nothing
            row.update(outcome="model_unavailable", checks=[], passed=False, cost_micros=0)
        else:
            row["cost_micros"] = 700_000 if n < 23 else 760_000
    # Counted: 19 at 700k and 20 at 760k, the median 760k. With the 4 zeros it would be 700k.
    g = design(results)
    assert g.not_run == 4
    assert g.rules_failed == ("median_cost",)
    assert g.median_cost_micros == 760_000


# --- runs that did not run ------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["model_unavailable", "outbound_blocked"])
def test_an_outage_is_neither_a_pass_nor_a_fail(outcome: str) -> None:
    results = rows()
    replace(results, "adv_d", 0, outcome=outcome)  # an adversarial run: every run must pass
    g = design(results)
    assert g.status == "passed"
    assert (g.runs, g.passes, g.not_run) == (18, 17, 1)
    assert g.categories["adversarial"].not_run == 1


def test_more_than_ten_percent_outages_in_a_group_is_incomplete() -> None:
    results = rows()  # songs: 6 runs; one outage is 17 % of the group, 5 % of the file
    replace(results, "pick_a", 0, outcome="model_unavailable")
    v = verdict(results)
    assert v.groups["songs"].status == "incomplete"
    assert v.groups["design"].status == "passed"


def test_ten_percent_outages_is_still_complete_and_more_in_the_file_is_not() -> None:
    results = rows({"s_a": 10, "s_b": 10, "s_c": 10, "s_d": 10, "pick_a": 10, "adv_s": 10})
    # 40 standard + 3 adversarial = 43 design runs, 20 song runs, 63 in the file
    for case, r in [("s_a", 0), ("s_b", 0), ("s_c", 0), ("s_d", 0)]:
        replace(results, case, r, outcome="model_unavailable")
    v = verdict(results)  # 4 of 43 design runs (9.3 %), 4 of 63 in the file
    assert v.groups["design"].status == "passed"
    for case, r in [("s_a", 1), ("s_b", 1)]:
        replace(results, case, r, outcome="outbound_blocked")
    v = verdict(results)  # 6 of 43 (14 %)
    assert v.groups["design"].status == "incomplete"
    assert v.groups["songs"].status == "passed"  # 6 of 63 in the file: under 10 %


def test_outages_over_the_share_in_the_file_make_every_group_incomplete() -> None:
    results = rows()  # 21 runs in the file, songs 6
    for r in range(3):
        replace(results, "s_a", r, outcome="model_unavailable")  # 3 of 21: 14 %
    v = verdict(results)
    assert {g.status for g in v.groups.values()} == {"incomplete"}


def test_exactly_the_share_of_outages_is_complete() -> None:
    results = rows(
        {"s_a": 10, "s_b": 10, "s_c": 10, "s_d": 10, "adv_d": 10, "pick_a": 10, "adv_s": 10}
    )
    # design 50 runs, songs 20, file 70: 5 design outages are exactly 10 % of the group
    for case in STANDARD:
        replace(results, case, 0, outcome="model_unavailable")
    replace(results, "adv_d", 0, outcome="model_unavailable")
    assert design(results).status == "passed"
    replace(results, "adv_d", 1, outcome="model_unavailable")
    assert design(results).status == "incomplete"


def test_a_case_none_of_whose_runs_ran_is_incomplete() -> None:
    results = rows({"s_b": 10, "s_c": 10, "s_d": 10, "adv_s": 10, "pick_a": 10})
    for r in range(3):  # 3 of 36 design runs, 3 of 56 in the file
        replace(results, "s_a", r, outcome="model_unavailable")
    assert design(results).status == "incomplete"


def test_incomplete_wins_over_a_failed_rule_so_an_outage_cannot_fail_a_model() -> None:
    results = rows()
    replace(results, "pick_a", 0, outcome="model_unavailable")
    replace(results, "adv_s", 0, failed=("forbidden_text",))
    g = songs(results)
    assert g.status == "incomplete"
    assert not g.passed
    assert g.categories["adversarial"].rules_failed == ("every_run", "forbidden_text")


# --- the file must be this suite's ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "rule"),
    [
        ({"suite_version": 3}, "suite_version"),
        ({"suite_version": 5}, "suite_version"),
        ({"manifest_digest": "e" * 64}, "manifest_digest"),
    ],
)
def test_results_of_another_suite_version_or_digest_never_pass(
    header: dict[str, Any], rule: str
) -> None:
    v = verdict(rows(), **header)
    for g in v.groups.values():
        assert g.status == "failed"
        assert g.rules_failed == (rule,)
        assert g.as_json()["passed"] is False


def test_another_suite_fails_even_when_it_would_be_incomplete() -> None:
    results = rows()
    for r in range(3):
        replace(results, "s_a", r, outcome="model_unavailable")
    assert {g.status for g in verdict(results, suite_version=1).groups.values()} == {"failed"}


@pytest.mark.parametrize(
    ("mutate", "rule"),
    [
        (lambda rs: rs.append(run("not_in_manifest", 0)), "unknown_case"),
        (lambda rs: rs[0].update(category="adversarial"), "category_mismatch"),
        (lambda rs: rs.append(copy.deepcopy(rs[0])), "duplicate_run"),
        (lambda rs: rs[0].update(passed=False), "inconsistent_row"),
        (lambda rs: rs[0]["checks"][0].update(ok=False), "inconsistent_row"),  # passed stays true
        (lambda rs: rs[0].update(checks=[]), "inconsistent_row"),  # no check is no pass
    ],
)
def test_a_file_that_is_not_this_suites_runs_fails_every_group(mutate: Any, rule: str) -> None:
    results = rows()
    mutate(results)
    v = verdict(results)
    assert v.groups
    for g in v.groups.values():
        assert g.status == "failed"
        assert rule in g.rules_failed


@pytest.mark.parametrize(
    "bad",
    [
        {"results_format": 1, "results": []},
        {"format": 1, "results": []},
        doc(rows(), results_format="2"),
        doc(rows(), results_format=2.0),
        doc(rows(), suite_version="4"),
        doc([{**run("s_a", 0), "repeat": True}]),
        doc([{**run("s_a", 0), "cost_micros": -1}]),
        doc([{k: v for k, v in run("s_a", 0).items() if k != "category"}]),
        doc([{**run("s_a", 0), "passed": "true"}]),
        {k: v for k, v in doc(rows()).items() if k != "manifest_digest"},
    ],
)
def test_a_malformed_file_is_refused_without_its_content(bad: dict[str, Any]) -> None:
    with pytest.raises(ResultsInvalid) as refused:
        evaluate(bad, MANIFEST)
    assert "s_a" not in str(refused.value)


# --- the stored verdict ------------------------------------------------------------------------


def db_check(verdict: Any, passed: Any) -> bool:
    """``model_eval_results_passed_matches_verdict`` (ADR 0012 section 5) in Python:
    ``jsonb_typeof(verdict) = 'object' AND verdict ? 'passed' AND jsonb_typeof(verdict ->
    'passed') = 'boolean' AND passed = (verdict ->> 'passed')::boolean``."""
    return (
        isinstance(verdict, dict)
        and "passed" in verdict
        and type(verdict["passed"]) is bool  # pyright: ignore[reportUnknownArgumentType]
        and type(passed) is bool
        and verdict["passed"] == passed
    )


def test_the_check_mirror_refuses_what_the_database_refuses() -> None:
    for bad in (
        {}, {"x": 1}, ["passed"], "passed", 1, True, None, [{"passed": True}],
        {"result": {"passed": True}}, {"passed": None}, {"passed": "true"}, {"passed": 1},
        {"passed": {}}, {"passed": []},
    ):  # fmt: skip
        assert not db_check(bad, True)
        assert not db_check(bad, False)
    assert not db_check({"passed": True}, False)
    assert db_check({"passed": True, "extra": 1}, True)


def _three() -> list[GroupVerdict]:
    passed = design(rows())
    results = rows()
    replace(results, "s_a", 0, failed=("limits",))
    failed = design(results)
    results = rows()
    for r in range(3):
        replace(results, "s_a", r, outcome="model_unavailable")
    incomplete = design(results)
    assert [g.status for g in (passed, failed, incomplete)] == ["passed", "failed", "incomplete"]
    return [passed, failed, incomplete]


def test_every_stored_verdict_satisfies_the_database_check() -> None:
    for g in _three():
        stored = json.loads(json.dumps(g.as_json(), allow_nan=False))
        assert db_check(stored, g.passed)
        assert stored["passed"] is g.passed
        assert stored["status"] == g.status
        assert stored["group"] == "design"
        assert (stored["suite_version"], stored["manifest_digest"]) == (4, DIGEST)


def test_an_incomplete_verdict_is_stored_with_passed_false() -> None:
    incomplete = _three()[2]
    assert incomplete.as_json()["passed"] is False
    assert incomplete.as_json()["status"] == "incomplete"


def test_the_stored_verdict_has_the_runs_and_the_failed_rules_per_category() -> None:
    failed = _three()[1].as_json()
    assert failed["categories"]["standard"] == {
        "runs": 12, "passes": 11, "not_run": 0, "rules_failed": ["limits"],
    }  # fmt: skip
    assert set(failed) == {
        "passed", "status", "group", "suite_version", "manifest_digest", "runs", "passes",
        "not_run", "median_cost_micros", "rules_failed", "categories",
    }  # fmt: skip
