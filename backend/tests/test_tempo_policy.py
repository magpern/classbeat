"""Tempo clashes between adjacent songs at the crossfade (#1040; ADR 0008 note of 2026-11-13).

``tempo_policy`` is pure; ``check_song_list`` shows its jumps as a soft note beside a valid answer.
A jump is never an error, never costs a correction and never stops ``submit_song_list``.
"""

import json
from typing import Any

import pytest

from classbeat.agent import choose_songs, tempo_policy
from classbeat.agent.choose_songs import ChooseContext, choose_stage
from classbeat.agent.dispatcher import dispatch
from classbeat.agent.provider import ToolCall
from classbeat.agent.songs import PoolTrack
from classbeat.agent.tempo_policy import TempoJump, relation, tempo_jumps
from classbeat.evals import pick
from classbeat.evals.suite import load_suite
from pickfake import songs, world
from songscripts import provider_for

ROLES = ["warmup", "build", "climb", "recovery", "sprint", "sprint", "recovery", "cooldown"]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (120, 124, "compatible"),
        (124, 120, "compatible"),
        (120, 120, "compatible"),
        (100, 106, "compatible"),  # the boundary: 6 % of the slower, inclusive
        (100, 106.01, "jump"),
        (120, 60, "half_double"),
        (120, 240, "half_double"),
        (60, 120, "half_double"),
        (120, 126, "compatible"),
        (120, 128, "jump"),
        (120, 150, "jump"),
        (150, 120, "jump"),
        (120, 63.6, "half_double"),  # half time, 6 % above
        (120, 63.7, "jump"),
        (120, 254.4, "half_double"),  # double time, 6 % above
        (120, 255, "jump"),
        (90, 120, "jump"),
        (0, 120, "compatible"),  # no usable tempo: not judged
        (float("nan"), 120, "compatible"),
        (-5, 120, "compatible"),
    ],
)
def test_the_relation_of_two_tempos(a: float, b: float, expected: str) -> None:
    assert relation(a, b) == expected


def test_jumps_name_their_positions_in_play_order() -> None:
    bpms = [100, 104, 150, 150, 75, 120, 120, 90]
    jumps = tempo_jumps(bpms, ROLES)
    assert [(j.position, j.from_bpm, j.to_bpm) for j in jumps] == [
        (1, 104, 150),
        (4, 75, 120),  # 150 to 75 is half time and not listed
        (6, 120, 90),
    ]
    assert jumps[0] == TempoJump(1, 104, 150, "build", "climb")
    assert jumps[0].phase_change
    assert not TempoJump(4, 1, 2, "sprint", "sprint").phase_change
    assert tempo_jumps([120, 124, 60, 120], ROLES) == ()  # compatible and half/double only
    assert tempo_jumps([120], ["warmup"]) == ()
    assert tempo_jumps([], []) == ()


def test_the_view_is_numbers_roles_and_one_fixed_sentence() -> None:
    assert tempo_policy.jumps_view(()) == {}
    many = [TempoJump(n, 100.4, 150.6, "build", "sprint") for n in range(20)]
    view = tempo_policy.jumps_view(many)
    assert view["tempo_note"] == tempo_policy.NOTE
    assert len(view["tempo_jumps"]) == tempo_policy.MAX_JUMPS_SHOWN
    assert view["tempo_jumps"][0] == {
        "from": 0,
        "to": 1,
        "bpm": [100, 151],
        "roles": ["build", "sprint"],
    }
    assert set(view) == {"tempo_jumps", "tempo_note"}


def context(bpms: list[float]) -> ChooseContext:
    w = world()
    tracks = [
        PoolTrack(f"SmokePick{n:013d}", f"Song {n}", f"Artist {n}", ROLES[n], bpm, 240)
        for n, bpm in enumerate(bpms)
    ]
    return ChooseContext.build(w.plan, tracks, 0)


def run(stage: Any, name: str, args: Any) -> dict[str, Any]:
    return json.loads(dispatch(stage, ToolCall("c1", name, args)).result.content)


LIST = songs(*[("pool", n, r) for n, r in enumerate(ROLES)])


def test_a_list_with_a_jump_gets_a_soft_note_naming_the_positions() -> None:
    stage = choose_stage(context([100, 104, 150, 148, 146, 144, 142, 140]))
    out = run(stage, "check_song_list", LIST)
    assert out["valid"] is True
    assert [(j["from"], j["to"], j["bpm"]) for j in out["tempo_jumps"]] == [(1, 2, [104, 150])]
    assert out["tempo_note"] == tempo_policy.NOTE
    assert "errors" not in out
    assert "tempo_jumps" in choose_songs.SYSTEM_PROMPT
    assert tempo_policy.TEMPO_GUIDANCE in choose_songs.SYSTEM_PROMPT


def test_a_list_without_a_jump_gets_no_note() -> None:
    out = run(
        choose_stage(context([100, 104, 108, 110, 112, 114, 112, 108])), "check_song_list", LIST
    )
    assert out["valid"] is True
    assert not {"tempo_jumps", "tempo_note"} & set(out)


def test_a_jump_never_blocks_a_list_or_uses_a_correction() -> None:
    stage = choose_stage(context([60, 200, 60, 200, 60, 200, 60, 200]))
    first = dispatch(stage, ToolCall("c1", "check_song_list", LIST))
    assert first.ok
    assert len(run(stage, "check_song_list", LIST)["tempo_jumps"]) >= 7
    done = dispatch(stage, ToolCall("c2", choose_songs.FINAL_TOOL, LIST))
    assert done.ok
    assert done.final
    assert len(done.output.songs) == 8  # the list is stored as sent; the jumps change nothing
    assert "tempo_jumps" not in done.result.content  # submit adds no second note


def test_an_error_still_comes_back_as_an_error_not_a_note() -> None:
    stage = choose_stage(context([60, 200, 60, 200, 60, 200, 60, 200]))
    out = run(stage, "check_song_list", songs(("pool", 0, "warmup")))
    assert "tempo_jumps" not in out
    assert out.get("error") or out.get("errors") or out.get("valid") is not True


async def test_the_eval_case_runs_the_tempo_flow_check() -> None:
    suite = load_suite()
    case = suite.song_cases["tempo_adjacent_30"]
    assert case.expect.tempo_flow
    result, _, _ = await pick.run_pick_case(case, provider_for(case), *_run_args(suite))
    assert result.outcome == "submitted"
    row = [c for c in result.checks if c.name == "tempo_flow"]
    assert len(row) == 1
    assert row[0].ok, row[0].detail
    assert result.failed_checks == []


def _run_args(suite: Any) -> tuple[Any, Any, Any]:
    from agentfake import CONFIG
    from pickfake import CHOOSE_LIMITS, POOL_LIMITS

    return CONFIG, (POOL_LIMITS, CHOOSE_LIMITS), suite.manifest.bpm_bands
