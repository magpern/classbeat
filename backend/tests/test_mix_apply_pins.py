"""#1390 (ADR 0042 section 3, "Every audio edit runs amendment B's one order of checks"): a cut pin
changes the composed total like any cut, so a pinned derivation runs ``checked``, the class length,
the remap and fit and the plan gates (``mix_apply_calc.derived`` with the overlay's pins, then
``fitted``), each refusal with a pin present. Pure: no I/O."""

import ast
from pathlib import Path
from typing import Any

import pytest

from agentfake import good_draft
from classbeat.agent.stage import ToolError
from classbeat.agent.studio_mix import StudioTrack
from classbeat.plan_gates import PlanGates
from classbeat.studio import mix_plan_policy
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal
from classbeat.studio.mix_pin_model import PinRecord
from classbeat.swedebeat_studio import mix_apply_calc
from classbeat.swedebeat_studio.draft import StudioMusic
from studiomaps import track_map

REFS = tuple(f"r{n}" for n in range(8))
DJ = MixChoices.dj_defaults()


def case() -> tuple[Proposal, StudioMusic, dict[str, Any]]:
    """Eight whole 240 s tracks of exact timing crossfaded 8 s (1864 s, a 1800 s class: 1620 to
    1980 s) and the fake's good draft made 1864 s."""
    tracks = tuple(StudioTrack(f"Song {n}", "Band", track_map(240.0), "exact") for n in range(8))
    music = StudioMusic(REFS, tracks)
    raw = {"tracks": [{"reference": r, "start_sec": 0.0, "end_sec": 240.0} for r in REFS]}
    proposed = mix_plan_policy.checked({**raw, "crossfades": [8] * 7}, REFS, music.facts())
    draft = good_draft()
    draft["segments"][-1]["intervals"][-1]["sec"] += 64
    return Proposal(REFS, proposed, 1800), music, draft


def end_pin(reference: str, at: float) -> PinRecord:
    return PinRecord.model_validate(
        {"reference": reference, "occurrence": 0, "edge": "end", "requested_sec": at,
         "mode": "exact", "confirmed": False}
    )  # fmt: skip


def run(pins: tuple[PinRecord, ...], gates: PlanGates | None = None) -> mix_apply_calc.Fitted:
    value, music, draft = case()
    spec = mix_apply_calc.derived(value, music, DJ, pins)
    return mix_apply_calc.fitted(draft, dict(value.spec), spec, music, gates)


def reason(pins: tuple[PinRecord, ...], gates: PlanGates | None = None) -> str:
    with pytest.raises(MixChoiceRefused) as caught:
        run(pins, gates)
    return caught.value.reason


def test_a_pinned_spec_composes_and_the_workout_follows_it() -> None:
    unpinned = run(())
    made = run((end_pin("r0", 200.0),))
    assert made.spec["tracks"][0]["end_pin"]["requested_sec"] == 200.0
    assert made.composed.plan["tracks"][0] == {"track": 0, "start_sec": 0.0, "end_sec": 200.0}
    assert made.composed.plan == mix_apply_calc.plan_of(REFS, made.spec)
    assert made.composed.duration_sec == 1824.0
    assert made.result.duration_sec < unpinned.result.duration_sec  # the remap moved the workout
    assert any("off_beat_cut" in h for h in made.composed.hints)  # 200.0 is off the 1.875 s bars


def test_without_a_pin_the_derivation_is_unchanged() -> None:
    value, music, _ = case()
    assert mix_apply_calc.derived(value, music, DJ) == mix_apply_calc.derived(value, music, DJ, ())


def test_step_1_checked_refuses_a_pin() -> None:
    assert reason((end_pin("r0", 250.0),)) == "cut_out_of_range"


def test_step_2_the_class_length_on_the_pinned_total() -> None:
    pins = tuple(end_pin(r, 120.0) for r in REFS[:3])  # 1864 - 360 = 1504 s, under 1620 s
    assert reason(pins) == "class_length_out_of_tolerance"


def test_step_3_the_fit_with_a_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object) -> None:
        raise ToolError("invalid_draft", ["no fit"])

    monkeypatch.setattr(mix_apply_calc, "check", refuse)
    assert reason((end_pin("r0", 200.0),)) == "workout_does_not_fit"


def test_step_4_the_plan_gates_with_a_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    short = PlanGates(max_class_minutes=28, games=True, races=True, ftp_tests=True)  # 1764 s
    assert reason((end_pin("r0", 200.0),), short) == "plan_class_too_long"

    def refuse(*_: object) -> None:
        raise ToolError("not_allowed", ["no games"])

    monkeypatch.setattr(mix_apply_calc, "within_plan", refuse)
    roomy = PlanGates(max_class_minutes=60, games=False, races=True, ftp_tests=True)
    assert reason((end_pin("r0", 200.0),), roomy) == "plan_not_allowed"


def test_the_first_step_that_fails_is_named() -> None:
    """A pin past the length and a class far too short: ``checked`` comes first."""
    pins = (end_pin("r0", 250.0), *(end_pin(r, 120.0) for r in REFS[1:4]))
    assert reason(pins) == "cut_out_of_range"


PURE = (
    "classbeat/studio/mix_pin_model.py",
    "classbeat/studio/mix_pin_policy.py",
    "classbeat/studio/mix_spec_model.py",
    "classbeat/studio/mix_plan_policy.py",
    "classbeat/studio/mix_hint_policy.py",
    "classbeat/studio/mix_choice_policy.py",
    "classbeat/studio/mix_applied_policy.py",
)
IO = ("sqlalchemy", "httpx", "classbeat.db", "classbeat.jobs", "classbeat.design", "os", "pathlib")


@pytest.mark.parametrize("path", PURE)
def test_the_pin_modules_import_no_io(path: str) -> None:
    """CLAUDE.md rule 3: the modules the pins touch import no database, job, network or file."""
    source = (Path(__file__).resolve().parent.parent / "src" / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    named = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    named += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in named if m.split(".")[0] in IO or m.startswith(IO)]
