"""The song stages' runs (ADR 0008 section 3, issue #179 criteria 3 and 4): the limits 12/30/3 and
8/12/3, the cost ceiling of the song kinds, identical ``system`` and ``tools`` on every run, and
the versions in the provenance model."""

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentfake import MemoryHost
from classbeat.agent.limits import Limits
from classbeat.agent.records import Provenance, provenance
from classbeat.agent.schemas import SONG_SCHEMA_VERSION
from classbeat.config import Settings
from classbeat.evals.pick import analysis_map
from classbeat.jobs.errors import JobFailure
from pickfake import (
    POOL_14,
    call,
    case,
    choose_stage_turns,
    entries,
    everything_sent,
    good_pick_provider,
    pool_stage_turns,
    provider,
    run_both,
    run_choose,
    run_pool,
    songs,
    turn,
    world,
)

SETTINGS = Settings()


async def failure_of(coro: Any) -> str:
    with pytest.raises(JobFailure) as raised:
        await coro
    return raised.value.category


def searches(n: int) -> list[dict[str, Any]]:
    return [turn(call("search_tracks", {"query": f"group{i % 4}"})) for i in range(n)]


# --- limits -----------------------------------------------------------------------------------


def test_the_song_stage_limits_follow_the_adr() -> None:
    from classbeat.agent import choose_songs, pick_pool

    pool = Limits().for_song_stage(
        SETTINGS, max_turns=pick_pool.MAX_TURNS, max_tool_calls=pick_pool.MAX_TOOL_CALLS
    )
    choose = Limits().for_song_stage(
        SETTINGS, max_turns=choose_songs.MAX_TURNS, max_tool_calls=choose_songs.MAX_TOOL_CALLS
    )
    assert (pool.max_turns, pool.max_tool_calls, pool.max_corrections) == (12, 30, 3)
    assert (choose.max_turns, choose.max_tool_calls, choose.max_corrections) == (8, 12, 3)
    for limits in (pool, choose):
        assert limits.stage_seconds == 300.0
        assert limits.job_cost_micros == 3_000_000  # CLASSBEAT_SONG_JOB_COST_MICROS, $3
    other = Settings(song_job_cost_micros=500_000)  # pyright: ignore[reportCallIssue]
    assert Limits().for_song_stage(other, max_turns=1, max_tool_calls=1).job_cost_micros == 500_000
    assert SETTINGS.agent_job_cost_micros == 1_500_000  # the design kinds' ceiling is its own


async def test_the_pool_stage_stops_after_twelve_turns() -> None:
    w, host = world(), MemoryHost()
    fake = provider(*searches(13))
    assert await failure_of(run_pool(w, fake, host)) == "limit_exceeded"
    assert fake.remaining == 1  # the 13th turn was never requested
    assert host.outcomes() == ["limit_exceeded"]


async def test_the_pool_stage_stops_at_thirty_tool_calls() -> None:
    w, host = world(), MemoryHost()
    fake = provider(turn(*[call("search_tracks", {"query": "group0"})] * 31))
    assert await failure_of(run_pool(w, fake, host)) == "limit_exceeded"
    [record] = host.runs.values()
    assert record["counters"].tool_calls == 30
    assert len(host.events) == 30


async def test_the_choose_stage_stops_after_eight_turns_and_twelve_tool_calls() -> None:
    w = world()
    ctx = w.choose_context(POOL_14)
    fake = provider(*[turn(call("get_pool")) for _ in range(9)])
    assert await failure_of(run_choose(ctx, fake, MemoryHost())) == "limit_exceeded"
    assert fake.remaining == 1
    host = MemoryHost()
    fake = provider(turn(*[call("get_pool")] * 13))
    assert await failure_of(run_choose(ctx, fake, host)) == "limit_exceeded"
    [record] = host.runs.values()
    assert record["counters"].tool_calls == 12


async def test_a_fourth_refused_submit_is_invalid_output_in_both_stages() -> None:
    w = world()
    bad_pool = entries((0, "build"))
    fake = provider(
        turn(call("search_tracks", {"query": "group0"})), *[turn(call("submit_pool", bad_pool))] * 4
    )
    assert await failure_of(run_pool(w, fake, MemoryHost())) == "invalid_output"
    ctx = w.choose_context(POOL_14)
    bad_list = songs(("pool", 0, "warmup"))
    fake = provider(*[turn(call("submit_song_list", bad_list))] * 4)
    host = MemoryHost()
    assert await failure_of(run_choose(ctx, fake, host)) == "invalid_output"
    [record] = host.runs.values()
    assert record["counters"].corrections == 4


async def test_three_refused_submits_may_be_corrected() -> None:
    w, host = world(), MemoryHost()
    bad = entries((0, "build"))
    fake = provider(
        turn(
            call("search_tracks", {"query": "group0"}), call("search_tracks", {"query": "group1"})
        ),
        *[turn(call("submit_pool", bad, expect={"ok": False}))] * 3,
        turn(call("submit_pool", entries(*POOL_14), expect={"accepted": True})),
    )
    out = await run_pool(w, fake, host)
    assert out.corrections == 3


# --- the cost ceiling -------------------------------------------------------------------------


async def test_the_song_ceiling_counts_what_the_job_spent_and_stops_before_the_turn() -> None:
    # $3 ceiling; the 8 000 token output reserve alone is $0.16 at the fake's $20 per million.
    w = world()
    host = MemoryHost(spent_before=2_900_000)
    fake = good_pick_provider()
    assert await failure_of(run_pool(w, fake, host)) == "limit_exceeded"
    assert fake.remaining == len(pool_stage_turns()) + len(choose_stage_turns())  # no turn asked
    host = MemoryHost(spent_before=2_000_000)
    fake = good_pick_provider()
    await run_both(world(), fake, host)
    assert host.outcomes() == ["submitted", "submitted"]


async def test_the_ceiling_is_across_the_stages_and_stops_the_second() -> None:
    w, host = world(), MemoryHost()
    fake = good_pick_provider()
    await run_pool(w, fake, host)
    spent = sum(r["counters"].cost_micros for r in host.runs.values())
    assert 0 < spent < 100_000
    host.spent_before = 3_000_000 - spent  # what the pool stage cost is already on the job
    ctx = w.choose_context(POOL_14)
    assert await failure_of(run_choose(ctx, fake, host)) == "limit_exceeded"


async def test_a_tight_ceiling_stops_a_long_pool_run_with_the_spend_below_it() -> None:
    w, host = world(), MemoryHost()
    tight = Limits(max_turns=12, max_tool_calls=30, max_corrections=3, job_cost_micros=400_000)
    fake = provider(*[{**t, "usage": {"input": 30_000, "output": 2_000}} for t in searches(12)])
    assert await failure_of(run_pool(w, fake, host, tight)) == "limit_exceeded"
    [record] = host.runs.values()
    assert 1 <= record["counters"].turns < 12
    assert record["counters"].cost_micros <= tight.job_cost_micros


# --- the same system and tools every time ------------------------------------------------------


async def test_two_runs_of_a_stage_send_identical_system_and_tools() -> None:
    sends = []
    for minutes in (30, 45):
        w = world(case(brief={"length_min": minutes, "climbs": 1, "sprints": 2}))
        fake = good_pick_provider()
        if minutes == 45:  # a longer class needs more songs: pick them by the same indexes
            w = world(case())
        await run_both(w, fake, MemoryHost())
        sends.append(fake.sent)
    for a, b in zip(sends[0], sends[1], strict=True):
        assert a.spec.system == b.spec.system
        assert a.spec.tools == b.spec.tools
        assert a.spec.final_tool == b.spec.final_tool
    pool_a, choose_a = sends[0]
    assert pool_a.spec.system != choose_a.spec.system
    assert [t.name for t in choose_a.spec.tools] == [
        "get_pool",
        "check_song_list",
        "submit_song_list",
    ]


async def test_the_first_message_holds_the_facts_and_the_computed_numbers() -> None:
    w, host = world(), MemoryHost()
    fake = good_pick_provider()
    await run_both(w, fake, host)
    pool_msg = json.loads(fake.sent[0].spec.first_message.split("\n\n", 1)[1])
    assert pool_msg["target_sec"] == 1800
    assert pool_msg["songs"] == 8
    assert pool_msg["pool_size"] == {"min": 8, "max": 32}
    assert pool_msg["crossfade_sec"] == 9.0
    assert pool_msg["mode"] == "pick"
    choose_msg = json.loads(fake.sent[1].spec.first_message.split("\n\n", 1)[1])
    assert choose_msg["pool_tracks"] == 14
    assert choose_msg["omitted_no_bpm"] == 0
    assert "kept_songs" not in choose_msg
    sent = everything_sent(fake)
    assert "SmokePick" not in sent


# --- provenance --------------------------------------------------------------------------------


async def test_the_prompt_versions_and_the_schema_version_go_into_the_provenance() -> None:
    w, host = world(), MemoryHost()
    pooled, chosen = await run_both(w, good_pick_provider(), host)
    m = analysis_map(w.catalogue.tracks[:3])
    prov = provenance(
        [pooled, chosen], m, origin="design_with_songs", schema_version=SONG_SCHEMA_VERSION
    )
    assert prov.schema_version == SONG_SCHEMA_VERSION == 1
    assert prov.stage_versions == {
        "pick_pool": pooled.info.prompt_version,
        "choose_songs": chosen.info.prompt_version,
    }
    assert prov.prompt_version == chosen.info.prompt_version
    assert prov.origin == "design_with_songs"
    assert prov.agent_run_ids == [pooled.run_id, chosen.run_id]
    assert prov.cost_micros == pooled.cost_micros + chosen.cost_micros
    assert Provenance.model_validate_json(prov.model_dump_json()) == prov


async def test_a_design_provenance_has_no_song_versions_and_validates_as_before() -> None:
    w = world()
    pooled, _ = await run_both(w, good_pick_provider(), MemoryHost())
    prov = provenance([pooled], analysis_map(w.catalogue.tracks[:3]))
    assert prov.schema_version is None
    assert prov.stage_versions is None
    assert prov.origin == "design_draft"


async def test_the_provenance_refuses_half_of_the_song_versions_and_a_bad_version() -> None:
    w = world()
    pooled, _ = await run_both(w, good_pick_provider(), MemoryHost())
    good = provenance(
        [pooled], analysis_map(w.catalogue.tracks[:3]), origin="design_with_songs", schema_version=1
    )
    data = good.model_dump()
    for edit in (
        {"stage_versions": None},
        {"schema_version": None},
        {"stage_versions": {"pick_pool": "not-a-hash"}},
        {"stage_versions": {"Bad Name": "0" * 64}},
        {"schema_version": 0},
    ):
        with pytest.raises(ValidationError):
            Provenance.model_validate({**data, **edit})
    with pytest.raises(ValidationError):  # a revise_draft still names its base and request
        Provenance.model_validate({**data, "origin": "revise_draft"})
    revised = Provenance.model_validate(
        {
            **data,
            "origin": "revise_songs",
            "base_revision_id": uuid4(),
            "request_message_id": uuid4(),
        }
    )
    assert revised.origin == "revise_songs"


def test_nothing_in_a_song_stage_has_a_write_tool_or_a_network_client() -> None:
    import ast
    from pathlib import Path

    agent = Path(__file__).resolve().parent.parent / "src" / "classbeat" / "agent"
    banned = {"httpx", "urllib", "requests", "socket", "sqlalchemy", "ssl", "anthropic", "mowl"}
    allowed_mowl = {"spotify"}  # the crossfade constant, in songs.py
    for name in ("pick_pool.py", "choose_songs.py", "registry.py", "songs.py"):
        tree = ast.parse((agent / name).read_text("utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.module == "mowl":
                    imported |= {f"mowl.{a.name}" for a in node.names if a.name not in allowed_mowl}
                else:
                    imported.add(node.module or "")
        assert {m for m in imported if m.split(".")[0] in banned} == set(), name
        assert not any("classbeat.connections" in m or "classbeat.spotify" in m for m in imported)
