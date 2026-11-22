"""The studio song stage measures whole songs (ADR 0036 amendment B, B6 and B6a; #1371).

Through the choose stage's tool handler with a studio ``ChooseContext`` (the plain sum, 10 % bounds
narrowed by the plan's maximum, ``MAX_TOTAL_SEC`` and a margin per song) and, for comparison, the
Spotify context (9.54 s per change, today's text). The empty bounds fail ``mix_too_long`` before any
model call. No network, no model, no database."""

import json
import math
import random
from typing import Any

import pytest

from agentfake import brief
from classbeat.agent.brief import Brief
from classbeat.agent.choose_songs import ChooseContext, choose_stage
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.provider import ToolCall
from classbeat.agent.songs import PickPlan, PoolTrack
from classbeat.agent.stage import Stage
from classbeat.jobs.errors import CATEGORIES, JobFailure
from classbeat.plan_gates import PlanGates, max_draft_seconds
from classbeat.studio.mix_plan_policy import MAX_TOTAL_SEC
from classbeat.swedebeat_studio import length_policy
from classbeat.swedebeat_studio.edit import another
from classbeat.swedebeat_studio.length_policy import (
    DURATION_SLACK_SEC,
    bounds,
    studio_total,
    unfillable,
    whole_songs_measure,
    within,
)
from classbeat.swedebeat_studio.pick import pick
from classbeat.swedebeat_studio.stages import Steps
from pickfake import songs

N = 12
ROLES = ["warmup", "build", "climb", "recovery", "sprint", "sprint", "build", "climb"]
ROLES = [*ROLES, "recovery", "sprint", "build", "cooldown"]
LIST_12 = songs(*[("pool", i, role) for i, role in enumerate(ROLES)])

PLAN_60 = PlanGates(max_class_minutes=60, games=True, races=True, ftp_tests=True)
PLAN_45 = PlanGates(max_class_minutes=45, games=True, races=True, ftp_tests=True)


def class_45() -> Brief:
    return brief(length_min=45)  # 2700 s: bounds 2430 to 2970 s


def pool_for(total: int, count: int = N) -> list[PoolTrack]:
    """``count`` tracks whose lengths sum to ``total`` (the roles of ``ROLES``)."""
    base, extra = divmod(total, count)
    return [
        PoolTrack(f"id{i:020d}", f"Song {i}", f"Artist {i}", ROLES[i], 120.0, base + (i < extra))
        for i in range(count)
    ]


def studio_stage(total: int, gates: PlanGates | None, b: Brief | None = None) -> Stage:
    plan = PickPlan.build(b or class_45())
    return choose_stage(
        ChooseContext.build(plan, pool_for(total), 0, measure=whole_songs_measure(gates))
    )


def spotify_stage(total: int) -> Stage:
    return choose_stage(ChooseContext.build(PickPlan.build(class_45()), pool_for(total), 0))


def check(stage: Stage, args: dict[str, Any] = LIST_12) -> dict[str, Any]:
    out = dispatch(stage, ToolCall("c1", "check_song_list", args)).result.content
    return json.loads(out)


def refusal(stage: Stage) -> str:
    out = check(stage)
    assert out["error"] == "invalid_song_list"
    (message,) = [m for m in out["messages"] if m.startswith("the total time")]
    return message


# --- the B6 example: 12 songs for a 45-minute class on a 60-minute plan ---------------------


def test_a_list_inside_the_margin_is_accepted_with_the_plain_sum() -> None:
    out = check(studio_stage(2880, PLAN_60))
    assert out["ok"] is True
    assert out["total_sec"] == 2880  # no 9.54 s per change of song subtracted


def test_the_per_song_margin_refuses_what_the_class_bounds_alone_would_allow() -> None:
    assert within(2965, 2700)  # inside 10 % over: `within` alone lets it through
    message = refusal(studio_stage(2965, PLAN_60))
    assert message == (
        "the total time is 2965 s (whole songs, no crossfade); it must be between 2442 and 2958 s"
    )


@pytest.mark.parametrize(("total", "accepted"), [(2440, False), (2445, True), (2958, True)])
def test_the_lower_end_and_the_upper_end(total: int, accepted: bool) -> None:
    out = check(studio_stage(total, PLAN_60))
    assert out.get("ok") is accepted, out


def test_the_spotify_pick_keeps_its_measure_and_text() -> None:
    # 2880 s of songs are 2880 - 11 * 9.54 = 2775.06 s there; 2965 is 2860 s and fits, 2440 is 2335.
    out = check(spotify_stage(2880))
    assert out["total_sec"] == round(2880 - 11 * 9.54)
    assert check(spotify_stage(2965))["ok"] is True
    message = refusal(spotify_stage(2440))
    assert message == (
        "the total time is 2335 s (lengths less 9.54 s per change of song); "
        "it must be between 2430 and 2970 s"
    )
    # the same list the studio refuses (2965 is outside its margin) the Spotify pick accepts
    assert check(studio_stage(2965, PLAN_60))["error"] == "invalid_song_list"


# --- the plan bound -------------------------------------------------------------------------


def test_the_plans_maximum_bounds_the_song_stage() -> None:
    assert max_draft_seconds(PLAN_45) == 2835
    assert bounds(2700, N, PLAN_45) == (2442, 2823)
    refused = refusal(studio_stage(2889, PLAN_45))  # 7 % over the class
    assert refused.endswith("must be between 2442 and 2823 s")
    assert check(studio_stage(2823, PLAN_45))["ok"] is True
    assert check(studio_stage(2824, PLAN_45))["error"] == "invalid_song_list"


def test_with_no_plan_gates_there_is_no_plan_bound() -> None:
    assert check(studio_stage(2889, None))["ok"] is True  # an input stored before #496
    assert bounds(2700, N, None) == (2442, 2958)


def test_the_largest_class_bound_is_the_mixs_maximum() -> None:
    huge = PlanGates(max_class_minutes=180, games=True, races=True, ftp_tests=True)
    _, high = bounds(9600, N, huge)  # 160 minutes: 10 % over is 10560 s, over MAX_TOTAL_SEC
    assert high == MAX_TOTAL_SEC - N * DURATION_SLACK_SEC
    problem = whole_songs_measure(huge).problem(MAX_TOTAL_SEC, 9600, N)  # no margin left
    assert problem is not None
    assert problem.endswith(f"and {MAX_TOTAL_SEC - N} s")


# --- empty bounds: mix_too_long, before any model call --------------------------------------


class NoModel:
    """A provider that raises when anything is asked of it."""

    name = "no-model"

    def start(self, spec: Any) -> Any:
        raise AssertionError("the model was called")


def steps_without_model(gates: PlanGates | None) -> Steps:
    nothing: Any = None  # never reached: the length check comes first
    return Steps(
        nothing, NoModel(), nothing, nothing, nothing, nothing, nothing, nothing, nothing, gates
    )


def test_the_error_category_exists() -> None:
    assert "mix_too_long" in CATEGORIES
    assert JobFailure("mix_too_long").category == "mix_too_long"


def test_a_class_no_list_can_fit_is_unfillable() -> None:
    long = PickPlan.build(brief(length_min=170))  # 10200 s of 150 minutes at most
    assert unfillable(long.target_sec, long.n, None)
    assert unfillable(long.target_sec, long.n, PLAN_60) is True
    fine = PickPlan.build(brief(length_min=160))
    assert not unfillable(fine.target_sec, fine.n, None)


async def test_a_pick_for_an_unfillable_class_fails_before_any_model_call() -> None:
    steps = steps_without_model(None)
    with pytest.raises(JobFailure) as caught:
        await pick(steps, brief(length_min=170), swap_anyway=False)
    assert caught.value.category == "mix_too_long"


async def test_another_song_and_the_choose_stage_check_it_too() -> None:
    steps = steps_without_model(None)
    plan = PickPlan.build(brief(length_min=170))
    with pytest.raises(JobFailure) as caught:
        await steps.choose(plan, [])
    assert caught.value.category == "mix_too_long"
    nothing: Any = None  # the seed is never read: the length check comes first
    with pytest.raises(JobFailure) as asked:
        await another(steps, brief(length_min=170), nothing, 0)
    assert asked.value.category == "mix_too_long"


# --- the margin covers every later length check (property) ----------------------------------


def lengths_accepted(
    rng: random.Random, target: int, count: int, gates: PlanGates | None
) -> list[int]:
    """Random lengths of ``count`` songs whose sum the studio measure accepts (or an empty list)."""
    low, high = bounds(target, count, gates)
    if low > high:
        return []
    total = rng.choice(
        [math.ceil(low), math.floor(high), rng.randint(math.ceil(low), math.floor(high))]
    )
    cuts = sorted(rng.sample(range(1, total), count - 1))
    return [b - a for a, b in zip([0, *cuts], [*cuts, total], strict=True)]


@pytest.mark.parametrize("gates", [None, PLAN_45, PLAN_60])
def test_an_accepted_list_moved_by_a_second_a_song_still_passes_every_later_check(
    gates: PlanGates | None,
) -> None:
    rng = random.Random(1371)  # noqa: S311 - a seeded property test, not a secret
    measure = whole_songs_measure(gates)
    seen = 0
    for _ in range(400):
        target = rng.choice([1800, 2400, 2700, 3000, 3600, 5400])
        count = rng.randint(3, 20)
        lengths = lengths_accepted(rng, target, count, gates)
        if not lengths:
            continue
        total = measure.total(lengths)
        assert measure.problem(total, target, count) is None
        seen += 1
        for shift in (-1, 1):  # a second, whatever the constant says
            moved = total + shift * count  # every length moved by up to 1 s, the worst case
            assert within(moved, target)
            assert moved <= MAX_TOTAL_SEC
            if gates is not None:
                assert moved <= max_draft_seconds(gates)
    assert seen > 100


def test_the_margin_is_not_zero_and_the_policy_is_the_one_check() -> None:
    assert DURATION_SLACK_SEC >= 1
    assert studio_total([240, 241]) == 481
    assert length_policy.UPPER == 0.10
