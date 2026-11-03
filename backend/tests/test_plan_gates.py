"""The plan's class length cap and feature gates without a database (#496): the brief check at
admit (``classbeat.quota.gates``), the 409 ``quota`` body of ``length`` and ``feature`` (no money),
and the worker's check of every draft in the design and revise stages, cycling and running
(``classbeat.plan_gates``, ``classbeat.agent.design.within_plan``)."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import analysed, brief, good_draft, script
from classbeat.agent import DesignContext, design_stage
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.model_input import MusicData
from classbeat.agent.provider import ToolCall
from classbeat.agent.revise import ReviseContext, revise_stage
from classbeat.agent.running import design_stage_for, revise_stage_for
from classbeat.agent.schemas import Brief, RunningDraft, WorkoutDraft
from classbeat.agent.stage import Stage
from classbeat.jobs.credits import QuotaRefused
from classbeat.plan_gates import PlanGates, draft_problems, max_draft_seconds
from classbeat.plans import Plan
from classbeat.quota.bounds import Bounds, effective
from classbeat.quota.gates import check_brief, gates_for

FREE = PlanGates(max_class_minutes=30, games=False, races=True, ftp_tests=True)
OPEN = PlanGates(max_class_minutes=90, games=True, races=True, ftp_tests=True)
SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat"


def refusal(gates: PlanGates, **values: Any) -> dict[str, Any] | None:
    try:
        check_brief(gates, {"length_min": 30, **values}, "free")
    except QuotaRefused as refused:
        return refused.detail()
    return None


# --- admit: the brief -------------------------------------------------------------------------


def test_a_brief_over_the_cap_is_a_length_refusal_with_minutes_and_no_money() -> None:
    assert refusal(FREE) is None  # exactly the cap
    assert refusal(FREE, length_min=31) == {
        "quota": "length", "limit": 30, "used": 31, "plan": "free",
    }  # fmt: skip
    assert refusal(OPEN, length_min=90) is None
    assert refusal(OPEN, length_min=91) == {
        "quota": "length", "limit": 90, "used": 91, "plan": "free",
    }  # fmt: skip


def test_races_and_ftp_tests_on_a_plan_without_them_are_feature_refusals() -> None:
    no_races = FREE.model_copy(update={"races": False})
    no_ftp = FREE.model_copy(update={"ftp_tests": False})
    assert refusal(no_races, races=0) is None
    assert refusal(no_races, races=1) == {"quota": "feature", "feature": "races", "plan": "free"}
    assert refusal(FREE, races=5) is None  # races allowed
    assert refusal(no_ftp, ftp_test=5) == {
        "quota": "feature", "feature": "ftp_tests", "plan": "free",
    }  # fmt: skip
    assert refusal(no_ftp, class_type="ftp_test") == {
        "quota": "feature", "feature": "ftp_tests", "plan": "free",
    }  # fmt: skip
    assert refusal(no_ftp, ftp_test=None, class_type="interval") is None
    assert refusal(FREE, ftp_test=20) is None


def test_a_running_brief_and_a_missing_brief_pass_the_feature_gates() -> None:
    closed = PlanGates(max_class_minutes=30, games=False, races=False, ftp_tests=False)
    running = Brief.model_validate({"sport": "running", "length_min": 30}).model_dump(mode="json")
    check_brief(closed, running, "free")  # running has no races, FTP tests or games
    check_brief(closed, None, "free")


def test_a_cost_body_still_shows_no_money_and_classes_still_count() -> None:
    assert QuotaRefused("cost", limit=5, used=9, plan="free").detail() == {
        "quota": "cost", "plan": "free",
    }  # fmt: skip
    assert QuotaRefused("classes", limit=3, used=3).detail() == {
        "quota": "classes", "limit": 3, "used": 3,
    }  # fmt: skip


def test_the_gates_are_a_strict_server_model() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PlanGates.model_validate({**FREE.model_dump(), "extra": 1})
    with pytest.raises(ValidationError, match="less than or equal to 180"):
        PlanGates.model_validate({**FREE.model_dump(), "max_class_minutes": 181})


def test_the_cap_a_job_carries_is_the_plans_under_the_environments_bound() -> None:
    pro = Plan("pro", "Pro", 20, 100, 25, 3, 200_000_000, "pro", frozenset(), 90, True, False, True)
    assert gates_for(pro, Bounds()) == PlanGates(
        max_class_minutes=90, games=True, races=False, ftp_tests=True
    )
    assert gates_for(pro, Bounds(class_minutes_max=45)).max_class_minutes == 45
    assert effective(pro, Bounds(class_minutes_max=45)).max_class_minutes == 45
    assert effective(pro, Bounds(class_minutes_max=180)).max_class_minutes == 90


# --- the worker: every draft ------------------------------------------------------------------


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> MusicData:
    return analysed(monkeypatch)


def call(stage: Stage, name: str, args: Any) -> tuple[dict[str, Any], Any]:
    done = dispatch(stage, ToolCall("c1", name, args))
    return json.loads(done.result.content), done


def with_game() -> dict[str, Any]:
    draft = good_draft()
    draft["segments"][1]["intervals"][0]["game"] = "racetrack"
    return draft


def with_ftp() -> dict[str, Any]:
    draft = good_draft()
    climb = draft["segments"][1]["intervals"]
    climb[0].update(sec=180, ftp_test=3)  # 240 s became a 3-minute test ...
    climb[2]["sec"] += 60  # ... and the class stays 30 minutes
    return draft


def test_draft_problems_name_each_gate_and_none_means_no_gate() -> None:
    spec = {"segments": [{"intervals": [{"game": "racetrack"}, {"ftp_test": 5}]}]}
    closed = PlanGates(max_class_minutes=10, games=False, races=False, ftp_tests=False)
    assert len(draft_problems(closed, spec, 10_000)) == 3
    assert draft_problems(OPEN, spec, 90 * 60) == []
    assert draft_problems(None, spec, 10_000) == []
    # The evals' 5 % tolerance: a draft fitted to the music may be a little longer.
    assert max_draft_seconds(FREE) == 1890
    assert draft_problems(FREE, {"segments": []}, 1890) == []
    assert draft_problems(FREE, {"segments": []}, 1891) != []


@pytest.mark.parametrize("tool", ["preview_draft", "submit_draft"])
def test_the_design_stage_refuses_a_game_the_plan_does_not_include(
    data: MusicData, tool: str
) -> None:
    gated = design_stage(DesignContext.build(brief(), data, None, FREE))
    body, done = call(gated, tool, {"draft": with_game()})
    assert (done.error, done.output) == ("not_allowed", None)
    assert body["messages"] == ["This instructor's plan does not include games: remove every game."]
    assert call(gated, tool, {"draft": good_draft()})[1].ok  # the same class without the game
    open_stage = design_stage(DesignContext.build(brief(), data, None, OPEN))
    assert call(open_stage, tool, {"draft": with_game()})[1].ok
    assert call(design_stage(DesignContext.build(brief(), data)), tool, {"draft": with_game()})[
        1
    ].ok


def test_the_design_stage_refuses_a_class_over_the_cap(data: MusicData) -> None:
    short = FREE.model_copy(update={"max_class_minutes": 20})
    body, done = call(
        design_stage(DesignContext.build(brief(), data, None, short)),
        "submit_draft",
        {"draft": good_draft()},  # 30 minutes
    )
    assert done.error == "not_allowed"
    assert "at most 20 minutes" in body["messages"][0]


def revise_ctx(data: MusicData, gates: PlanGates | None, *, songs: bool = False) -> ReviseContext:
    base = WorkoutDraft.model_validate(good_draft())
    return ReviseContext.build(
        brief(), data, base, (), "Add a game", music_changed=False, songs_changed=songs,
        gates=gates,
    )  # fmt: skip


@pytest.mark.parametrize("songs", [False, True])  # revise_draft, and revise_songs' phase two
@pytest.mark.parametrize("tool", ["preview_draft", "submit_draft"])
def test_a_revise_that_adds_a_game_or_an_ftp_test_the_plan_lacks_is_refused(
    data: MusicData, tool: str, songs: bool
) -> None:
    stage = revise_stage(revise_ctx(data, FREE, songs=songs))
    args: dict[str, Any] = {"draft": with_game()}
    if tool == "submit_draft":
        args["note"] = "Added a game."
    body, done = call(stage, tool, args)
    assert (done.error, done.output) == ("not_allowed", None)
    assert "games" in body["messages"][0]
    no_ftp = FREE.model_copy(update={"ftp_tests": False, "games": True})
    ftp = {**args, "draft": with_ftp()}
    assert call(revise_stage(revise_ctx(data, no_ftp, songs=songs)), tool, ftp)[1].error == (
        "not_allowed"
    )
    assert call(revise_stage(revise_ctx(data, OPEN, songs=songs)), tool, args)[1].ok


def running_draft() -> dict[str, Any]:
    return json.loads(json.dumps(script("running_first_draft").drafts["good"]))


def test_the_running_stages_hold_the_length_cap(data: MusicData) -> None:
    run = Brief.model_validate({"sport": "running", "length_min": 30})
    tiny = PlanGates(max_class_minutes=10, games=False, races=False, ftp_tests=False)
    design = design_stage_for(DesignContext.build(run, data, None, tiny))
    body, done = call(design, "submit_draft", {"draft": running_draft()})
    assert done.error == "not_allowed"
    assert "at most 10 minutes" in body["messages"][0]
    revise = revise_stage_for(
        ReviseContext.build(
            run,
            data,
            RunningDraft.model_validate(running_draft()),
            (),
            "Longer",
            music_changed=False,
            gates=tiny,
        )
    )
    args = {"draft": running_draft(), "note": "Kept."}
    assert call(revise, "submit_draft", args)[1].error == "not_allowed"
    assert call(revise, "preview_draft", {"draft": running_draft()})[1].error == "not_allowed"
    roomy = tiny.model_copy(update={"max_class_minutes": 90})
    assert call(design_stage_for(DesignContext.build(run, data, None, roomy)), "submit_draft", {
        "draft": running_draft()
    })[1].ok  # fmt: skip


# --- prices stay in the admin area ------------------------------------------------------------

PRICE_COLUMNS = ("monthly_price_cents", "intro_price_cents", "intro_price_until")
# Where a price column may be named: its table's mirror and the admin area's edit and audit.
PRICE_MODULES = frozenset(
    {
        SRC / "plans" / "tables.py",
        SRC / "admin" / "settings.py",
        SRC / "admin" / "audit.py",
    }
)


def test_no_module_outside_the_admin_area_names_a_price() -> None:
    """Billing is off (ADR 0011 section 8): nothing but the admin area reads or returns a price,
    so no public route or the user's plan can show one."""
    offenders = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        if path not in PRICE_MODULES
        and any(column in path.read_text(encoding="utf-8") for column in PRICE_COLUMNS)
    ]
    assert offenders == []
