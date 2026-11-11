"""The plan's class length cap and feature gates without a database (#496): the brief check at
admit (``classbeat.quota.gates``), the 409 ``quota`` body of ``length`` and ``feature`` (no money),
and the worker's check of every draft in the design and revise stages, cycling and running
(``classbeat.plan_gates``, ``classbeat.agent.design.within_plan``)."""

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agentfake import analysed, brief, good_draft, script
from classbeat.agent import DesignContext, design_stage
from classbeat.agent.brief import Brief
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.model_input import MusicData
from classbeat.agent.provider import ToolCall
from classbeat.agent.revise import ReviseContext, revise_stage
from classbeat.agent.running import design_stage_for, revise_stage_for
from classbeat.agent.schemas import RunningDraft, WorkoutDraft
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


# --- prices stay in the admin area: a lint ----------------------------------------------------
#
# The real guard is the database privilege (#510, migration 0030_plan_price_privilege, guard 2 in
# ``tests/role_guard.py``): ``classbeat_app`` holds ``SELECT`` on every column of ``plans`` but the
# prices and their currency, so any read of a price as the app role, whatever the query looks like
# (``SELECT p``, ``(p).*``, ``jsonb_agg(p)``, an aliased import, ``getattr``), fails with
# "permission denied" (``tests/test_plans_schema.py``, ``test_the_app_role_cannot_read_a_price``).
# This source scan only points at the module early, in review; it is known to miss deliberate or
# unusual shapes and is not a security boundary. ``currency`` is left out here because the word
# is part of "concurrency"; the privilege covers it.

PRICE_COLUMNS = ("monthly_price_cents", "intro_price_cents", "intro_price_until")
# Where a price column may be named or ``plans`` read whole: its table's mirror and the admin
# area's edit (three modules since #762; the route left settings.py for plans.py in #809) and audit
# (ADR 0011, 2026-11-03 amendment).
PRICE_MODULES = frozenset(
    {
        SRC / "plans" / "tables.py",
        SRC / "admin" / "plans.py",
        SRC / "admin" / "plan_rules.py",  # split out of settings.py (#762)
        SRC / "admin" / "settings_models.py",
        SRC / "admin" / "audit.py",
        # The one reader of a monthly price for a quote (ADR 0031 section 7, #948): opens no
        # session, reads with the maint session of ``billing_session`` or ``admin_session`` it is
        # given. ``test_billing_architecture`` pins who may import it.
        SRC / "billing" / "prices.py",
    }
)
_NAMES_PLANS = re.compile(r"\b(from|join|update|into|table)\s+(public\.)?plans\b", re.IGNORECASE)
_WHOLE_ROW = re.compile(
    r"(\bselect\s+(all\s+|distinct\s+)?\*"  # SELECT *
    r"|\b\w+\s*\.\s*\*"  # p.* (any alias: the statement names plans)
    r"|\breturning\s+\*"  # RETURNING *
    r"|\b(row_to_json|to_jsonb?|jsonb?_populate_record|hstore)\s*\(\s*\w+\s*\)"  # whole row
    r"|\btable\s+(public\.)?plans\b)",  # TABLE plans
    re.IGNORECASE,
)


def _literals(tree: ast.AST) -> list[str]:
    """Every string literal, an f-string's constant parts joined with ``?`` for each value (an
    implicit concatenation is already one literal)."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            found.append(
                "".join(
                    v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "?"
                    for v in node.values
                )
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
    return found


def price_offences(source: str) -> list[str]:
    """How ``source`` could read a price: naming a price column, a whole-row read of ``plans``
    in SQL text (``SELECT *``, ``p.*``, ``RETURNING *``, ``to_jsonb(p)``, ``TABLE plans``), or the
    SQLAlchemy ``plans`` table itself (``select(plans)`` returns every column)."""
    offences = [f"names {c}" for c in PRICE_COLUMNS if c in source]
    tree = ast.parse(source)
    for literal in _literals(tree):
        if _NAMES_PLANS.search(literal) and _WHOLE_ROW.search(literal):
            offences.append("reads plans whole")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("plans.tables"):
            if any(alias.name in ("plans", "*", "metadata") for alias in node.names):
                offences.append("imports the plans table")
        elif isinstance(node, ast.Attribute) and node.attr == "plans":
            base = node.value
            name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
            if name == "tables":
                offences.append("uses tables.plans")
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "plans"
        ):
            offences.append("looks up the plans table by name")
    return offences


@pytest.mark.parametrize(
    "source",
    [
        'Q = "SELECT monthly_price_cents FROM plans"',
        'Q = text("SELECT * FROM plans WHERE id = :p")',
        'Q = text("select distinct * from public.plans")',
        'Q = "SELECT p.* FROM entitlements e JOIN plans p ON p.id = e.plan_id"',
        'Q = ("SELECT e.id, "\n     "q.* FROM entitlements e JOIN plans q ON q.id = e.plan_id")',
        'Q = f"SELECT * FROM plans WHERE {WHERE}"',
        'Q = "UPDATE plans SET rank = 1 RETURNING *"',
        'Q = "SELECT to_jsonb(p) FROM plans p"',
        'Q = "SELECT row_to_json(x) FROM plans x"',
        'Q = "TABLE plans"',
        "from classbeat.plans.tables import plans\nq = select(plans)",
        "from classbeat.plans.tables import FREE, plans as p",
        "from classbeat.plans.tables import *",
        "from classbeat.plans import tables\nq = select(tables.plans)",
        "import classbeat.plans.tables\nq = select(classbeat.plans.tables.plans)",
        "from classbeat.plans.tables import metadata\nq = metadata.tables['plans']",
    ],
)
def test_the_price_guard_catches_every_way_to_read_a_price(source: str) -> None:
    assert price_offences(source) != []


@pytest.mark.parametrize(
    "source",
    [
        'Q = "SELECT id, name, rank, route_key FROM plans ORDER BY rank"',
        'Q = "SELECT count(*) FROM jobs"',
        'Q = "SELECT * FROM jobs WHERE id = :j"',  # another table
        "from classbeat.plans.tables import FREE, FEATURES",
        "plans = []\nplans.append(1)",
    ],
)
def test_the_price_guard_leaves_explicit_reads_alone(source: str) -> None:
    assert price_offences(source) == []


def test_no_module_outside_the_admin_area_names_a_price() -> None:
    """A lint (the privilege is the guard, above): billing is off (ADR 0011 section 8) and only
    the admin area, as ``classbeat_maint``, reads or returns a price. Every other read of
    ``plans`` names its columns, or the app role's query fails at the database."""
    offenders = {
        str(path.relative_to(SRC)): found
        for path in sorted(SRC.rglob("*.py"))
        if path not in PRICE_MODULES and (found := price_offences(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}
