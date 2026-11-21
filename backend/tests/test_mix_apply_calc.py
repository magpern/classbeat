"""#1369 (ADR 0036 amendment B, B4 and B6b) without a database: the proposal allowlist, the order of
the derivation's checks (``mix_apply.computed``, which the preview, the job's admit and its run
share), the fit's refusals (each call it guards made to raise), the purity of the calculation
modules and the preview's rate limit."""

import ast
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from agentfake import good_draft
from classbeat.agent.stage import ToolError
from classbeat.agent.studio_mix import StudioTrack
from classbeat.classes.mix_preview_limit import PreviewLimiter
from classbeat.plan_gates import PlanGates
from classbeat.studio import mix_plan_policy, mix_proposal_policy
from classbeat.studio.mix_choice_model import MixChoiceRefused, MixChoices, Proposal
from classbeat.studio.mix_facts_model import TrackFacts
from classbeat.swedebeat_studio import mix_apply, mix_apply_calc
from classbeat.swedebeat_studio.draft import StudioMusic
from mix_choice_fixtures import FACTS, ORDER, PLAIN, spec, track
from studiomaps import track_map

DJ = {"dj_mix": True, "blend": True, "trim": True}
WHOLE = {"dj_mix": False, "blend": False, "trim": False}


class Music:
    """The part of ``StudioMusic`` the derivation reads before the fit: the stored facts."""

    def __init__(self, facts: dict[str, TrackFacts]) -> None:
        self._facts = facts

    def facts(self) -> dict[str, TrackFacts]:
        return self._facts


def found(value: Proposal, facts: dict[str, TrackFacts]) -> mix_apply.mix_proposal.Resolved:
    draft = mix_apply.mix_proposal.ProposalDraft(uuid4(), {}, cast(Any, None))
    return mix_apply.mix_proposal.Resolved(draft, cast(Any, Music(facts)), value)


def reason(value: Proposal, facts: dict[str, TrackFacts], choices: Any) -> str:
    with pytest.raises(mix_apply.MixApplyRefused) as caught:
        mix_apply.computed(found(value, facts), found(value, facts).draft, choices)
    return caught.value.reason


SHORT = track("exact", 50.0, 10.0, 40.0)  # a song under 60 s that plays as a loop run
LOOPED = spec(("a", 0.0, 240.0), ("e", 0.0, 40.0), ("e", 10.0, 50.0), crossfades=[8, 0])
LONG = track("exact", 3000.0, 600.0, 1200.0)  # whole, four of them are over 150 minutes
CUT_LONG = spec(*[(r, 0.0, 600.0) for r in ORDER], crossfades=[0, 0, 0])


@pytest.mark.parametrize(
    ("value", "facts", "choices", "expected"),
    [
        # the choices first: a position off the list, a form that is not whole songs, a stray key
        (Proposal(ORDER, PLAIN, 880), FACTS, {**DJ, "keep_whole": [4]}, "invalid_input"),
        (Proposal(ORDER, PLAIN, 880), FACTS, {**WHOLE, "trim": True}, "invalid_input"),
        (Proposal(ORDER, PLAIN, 880), FACTS, [], "invalid_input"),
        # ``checked``'s reasons a choice can reach (B4 step 3), named and never repaired
        (Proposal(("a", "e"), LOOPED, 300), {"a": FACTS["a"], "e": SHORT}, WHOLE,
         "window_too_short"),
        (Proposal(ORDER, CUT_LONG, 2400), dict.fromkeys(ORDER, LONG), WHOLE, "mix_too_long"),
        # the class length (B6), both ways
        (Proposal(ORDER, PLAIN, 600), FACTS, WHOLE, "class_length_out_of_tolerance"),
        (Proposal(ORDER, PLAIN, 1000), FACTS, DJ, "class_length_out_of_tolerance"),
    ],
)  # fmt: skip
def test_each_reason_of_the_derivation_in_its_order(
    value: Proposal, facts: dict[str, TrackFacts], choices: Any, expected: str
) -> None:
    assert reason(value, facts, choices) == expected


def test_a_mix_too_long_comes_before_the_class_length() -> None:
    # 12 000 s of whole songs: over 150 minutes and over the class's 2640 s; the first is named.
    value = Proposal(ORDER, CUT_LONG, 2400)
    assert reason(value, dict.fromkeys(ORDER, LONG), WHOLE) == "mix_too_long"


def test_the_class_length_refusal_carries_the_total_and_the_range() -> None:
    value = Proposal(ORDER, PLAIN, 600)
    with pytest.raises(mix_apply.MixApplyRefused) as caught:
        mix_apply.computed(found(value, FACTS), found(value, FACTS).draft, WHOLE)
    assert caught.value.total_sec == 960.0
    assert caught.value.counts == {"total_sec": 960, "low_sec": 540, "high_sec": 660}


def test_without_the_fit_a_good_choice_is_derived_from_the_proposal() -> None:
    value = Proposal(ORDER, PLAIN, 880)
    made = mix_apply.computed(found(value, FACTS), found(value, FACTS).draft, WHOLE)
    assert made.fitted is None
    assert [(t["start_sec"], t["end_sec"]) for t in made.spec["tracks"]] == [(0.0, 240.0)] * 4
    assert made.spec["crossfades"] == [0, 0, 0]


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("design_draft", "proposal"),
        ("revise_draft", "proposal"),
        ("mix_choices", "pass"),
        ("duplicate", "pass"),
        ("beat_one", "pass"),
        ("sketch", "pass"),
        ("design_with_songs", "pass"),
        ("brand_new", "pass"),  # an origin nobody listed is no proposal (an allowlist)
        (None, "pass"),
        (7, "pass"),
    ],
)
def test_the_proposal_is_an_allowlisted_origin(origin: object, expected: str) -> None:
    assert mix_proposal_policy.step(studio_draft=True, same_list=True, origin=origin) == expected


@pytest.mark.parametrize(("studio", "same"), [(False, True), (True, False), (False, False)])
def test_the_walk_stops_off_the_list_or_off_the_studio(studio: bool, same: bool) -> None:
    for origin in ("design_draft", "mix_choices"):
        assert (
            mix_proposal_policy.step(studio_draft=studio, same_list=same, origin=origin) == "stop"
        )


def test_the_preview_limit_is_per_user_and_per_window() -> None:
    now = [0.0]
    limiter = PreviewLimiter(per_user=2, clock=lambda: now[0])
    one, two = uuid4(), uuid4()
    assert [limiter.allow(one) for _ in range(3)] == [True, True, False]
    assert limiter.allow(two)  # another user has its own count
    now[0] = 60.0  # the next window
    assert limiter.allow(one)


PURE = ("classbeat/swedebeat_studio/mix_apply_calc.py", "classbeat/studio/mix_proposal_policy.py")
IO = (
    "sqlalchemy",
    "httpx",
    "classbeat.db",
    "classbeat.jobs",
    "classbeat.design",
    "classbeat.connections",
)


@pytest.mark.parametrize("path", PURE)
def test_the_pure_modules_import_no_io(path: str) -> None:
    """CLAUDE.md rule 3: no import of the database, the jobs, the network or a module that has
    them (``swedebeat_studio.draft`` is read through a protocol)."""
    source = (Path(__file__).resolve().parent.parent / "src" / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    named = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    named += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in named if m.startswith(IO) or m.startswith("classbeat.swedebeat_studio")]


# --- The fit (B4a), each refusal it swallows made to raise -------------------------------------


def fit_case() -> tuple[Proposal, Any, dict[str, Any]]:
    """Eight whole 240 s tracks crossfaded 8 s (1864 s) and the fake's good draft made 1864 s."""
    refs = tuple(f"r{n}" for n in range(8))
    tracks = tuple(StudioTrack(f"Song {n}", "Band", track_map(240.0)) for n in range(8))
    music = StudioMusic(refs, tracks)
    raw = {"tracks": [{"reference": r, "start_sec": 0.0, "end_sec": 240.0} for r in refs]}
    proposed = mix_plan_policy.checked({**raw, "crossfades": [8] * 7}, refs, music.facts())
    draft = good_draft()
    draft["segments"][-1]["intervals"][-1]["sec"] += 64
    return Proposal(refs, proposed, 1800), music, draft


def fit(gates: PlanGates | None = None) -> mix_apply_calc.Fitted:
    value, music, draft = fit_case()
    spec = mix_apply_calc.derived(value, music, MixChoices.whole_songs())
    return mix_apply_calc.fitted(draft, dict(value.spec), spec, music, gates)


def test_the_fit_moves_the_workout_to_the_whole_songs_end() -> None:
    made = fit()
    assert made.result.duration_sec == 1920  # the end followed the music (1864 s to 1920 s)


def test_a_check_refusal_is_workout_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object) -> None:
        raise ToolError("invalid_draft", ["no fit"])

    monkeypatch.setattr(mix_apply_calc, "check", refuse)
    with pytest.raises(MixChoiceRefused) as caught:
        fit()
    assert caught.value.reason == "workout_does_not_fit"


def test_a_spec_that_does_not_compose_is_a_broken_invariant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_: object) -> None:
        raise ToolError("invalid_mix", ["no"])

    monkeypatch.setattr(mix_apply_calc, "compose", refuse)
    with pytest.raises(ValueError, match="does not compose"):
        fit()


@pytest.mark.parametrize(
    ("gates", "expected"),
    [
        (PlanGates(max_class_minutes=30, games=True, races=True, ftp_tests=True),
         "plan_class_too_long"),  # 1890 s at most
        (PlanGates(max_class_minutes=60, games=False, races=True, ftp_tests=False), None),
    ],
)  # fmt: skip
def test_the_plan_gates_on_the_fitted_class(gates: PlanGates, expected: str | None) -> None:
    if expected is None:
        assert fit(gates).result.duration_sec == 1920  # no game, no FTP test: allowed
        return
    with pytest.raises(MixChoiceRefused) as caught:
        fit(gates)
    assert caught.value.reason == expected


def test_another_plan_problem_is_plan_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object) -> None:
        raise ToolError("not_allowed", ["no games"])

    monkeypatch.setattr(mix_apply_calc, "within_plan", refuse)
    with pytest.raises(MixChoiceRefused) as caught:
        fit(PlanGates(max_class_minutes=60, games=False, races=True, ftp_tests=True))
    assert caught.value.reason == "plan_not_allowed"
