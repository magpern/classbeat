"""``OpenAIProvider`` on the Responses API (ADR 0012 §3, M3c P2, issue #204), offline.

Two kinds of answer, both under the real one-host allowlist and the test key: the hand-written
fixtures of ``tests/provider_fixtures/openai/`` (the documented response shapes, for the stops,
the errors and usage), and ``openaifake.ScriptedResponses``, which plays the ``FakeProvider``
scripts through the adapter, so real stages, the smoke cases and the planted-value test run
through it and every request body is checked as sent.
"""

import dataclasses
import itertools
import json
import subprocess
import sys
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr

from agentfake import CONFIG, MemoryHost, Planted, analysed, brief, from_dict, synthetic_tracks
from agentfake import provider as script_provider
from classbeat.agent import DesignContext, design_stage, run_stage
from classbeat.agent.catalog import CATALOG, EFFORTS, models_of
from classbeat.agent.prices import DEFAULT_PRICES, OPENAI_LONG_CONTEXT_TOKENS, PRICES_VERSION
from classbeat.agent.provider import ProviderError, StageSpec, ToolDef, ToolResult, Usage
from classbeat.agent.providers import OpenAIProvider
from classbeat.agent.providers.openai_common import strict_schema
from classbeat.agent.providers.openai_responses import request_shape
from classbeat.agent.stage import prompt_version
from classbeat.evals.cases import load_cases
from classbeat.evals.pick import load_pick_cases, run_pick_case
from classbeat.evals.runner import run_case
from openaifake import OPENAI_MODEL, PATH, check_request, scripted_openai
from pickfake import CHOOSE_LIMITS, POOL_LIMITS
from pickscripts import provider_for_case as pick_provider_for_case
from providerreplay import TEST_KEY, Answer, Replay
from smokescripts import provider_for_case
from test_agent_model_input import every_call_script

OPENAI = dataclasses.replace(CONFIG, model=OPENAI_MODEL)
TRACK: dict[str, Any] = {
    "type": "object",
    "properties": {
        "track": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
    },
    "additionalProperties": False,
}
SPEC = StageSpec(
    stage="design",
    system="You design classes.",
    tools=(ToolDef("get_music", "The music.", TRACK),),
    first_message="A brief.",
    model=OPENAI_MODEL,
    effort="high",
    max_tokens=32000,
)


def _fixture_provider(*names: str, **kw: Any) -> tuple[OpenAIProvider, Replay]:
    answers = [Answer.from_file("openai", n, path=PATH) for n in names]
    replay = Replay("openai", answers)
    return OpenAIProvider(SecretStr(TEST_KEY), transport=replay.transport(), **kw), replay


async def _design_run(monkeypatch: pytest.MonkeyPatch, script: str = "good_first_draft") -> Any:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    fake = script_provider(script)
    model, api = scripted_openai(fake)
    outcome = await run_stage(MemoryHost(), model, design_stage(ctx), ctx.first_message(), OPENAI)
    assert api.problems == []
    assert fake.remaining == 0
    return outcome, api, design_stage(ctx)


# --- the request ---------------------------------------------------------------------------


async def test_the_request_has_exactly_the_decided_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _, api, stage = await _design_run(monkeypatch)
    first = api.bodies[0]
    assert set(first) == {
        "model",
        "instructions",
        "input",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "store",
        "include",
        "max_output_tokens",
        "reasoning",
        "prompt_cache_key",
        "stream",
    }
    assert check_request(first) == []
    assert first["stream"] is False
    assert first["store"] is False
    assert first["include"] == ["reasoning.encrypted_content"]
    assert first["model"] == OPENAI_MODEL
    assert first["reasoning"] == {"effort": "medium"}  # CONFIG's effort, mapped
    assert first["max_output_tokens"] == OPENAI.max_tokens
    assert first["instructions"] == stage.system
    definitions = stage.definitions()
    assert first["tools"] == [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": strict_schema(t.input_schema),
            "strict": True,
        }
        for t in definitions
    ]
    version = prompt_version(stage.system, definitions)
    assert first["prompt_cache_key"] == f"design-{version[:40]}"
    [message] = first["input"]  # the first message alone
    assert message == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": message["content"][0]["text"]}],
    }
    assert "Invented" in message["content"][0]["text"]


async def test_two_runs_of_a_stage_send_the_same_instructions_tools_and_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, one, _ = await _design_run(monkeypatch)
    _, two, _ = await _design_run(monkeypatch)
    fixed = ("instructions", "tools", "prompt_cache_key", "model", "reasoning", "include")
    shapes = [json.dumps([b[k] for k in fixed]) for b in [*one.bodies, *two.bodies]]
    assert len(set(shapes)) == 1
    raw = [r.content for r in [*one.requests, *two.requests]]
    assert raw[0] == raw[len(one.requests)]  # the first requests are byte-identical


def test_the_cache_key_is_the_stage_and_prompt_version_only() -> None:
    key = request_shape(SPEC)["prompt_cache_key"]
    assert key == f"design-{prompt_version(SPEC.system, SPEC.tools)[:40]}"
    other = dataclasses.replace(SPEC, first_message="Another user's brief.", effort="low")
    assert request_shape(other) == request_shape(SPEC)
    assert len(key) <= 64


# --- the history ---------------------------------------------------------------------------


async def test_the_history_is_append_only_and_parallel_calls_are_answered_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ScriptedResponses`` refuses any other history (``api.problems``); here, also directly."""
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    fake = from_dict(every_call_script())
    model, api = scripted_openai(fake)
    await run_stage(MemoryHost(), model, design_stage(ctx), ctx.first_message(), OPENAI)
    assert api.problems == []
    bodies = api.bodies
    assert len(bodies) == 3
    for before, after in itertools.pairwise(bodies):
        assert after["input"][: len(before["input"])] == before["input"]
    # request 2 = request 1, its answer unchanged (reasoning with its encrypted content), the
    # reminder for the tool-less end_turn; request 3 then answers the six parallel calls in order
    second = bodies[1]["input"]
    assert second[1] == {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "enc-1",
    }
    assert second[-1]["role"] == "user"
    third = bodies[2]["input"][len(second) :]
    calls = [i for i in third if i["type"] == "function_call"]
    outputs = [i for i in third if i["type"] == "function_call_output"]
    assert len(calls) == 6
    assert [o["call_id"] for o in outputs] == [c["call_id"] for c in calls]
    assert third.index(outputs[0]) > third.index(calls[-1])  # answered together, after the calls
    assert {i["type"] for i in third} == {"reasoning", "function_call", "function_call_output"}


async def test_a_failed_turn_appends_nothing() -> None:
    model, replay = _fixture_provider("error_400", "end_turn", max_retries=0)
    conversation = model.start(SPEC)
    with pytest.raises(ProviderError):
        await conversation.turn([])
    turn = await conversation.turn([])
    assert turn.stop == "end_turn"
    assert replay.bodies[0]["input"] == replay.bodies[1]["input"]
    assert len(replay.bodies[1]["input"]) == 1


async def test_the_first_turn_sends_no_results_and_a_later_one_needs_some() -> None:
    model, replay = _fixture_provider("end_turn")
    conversation = model.start(SPEC)
    with pytest.raises(ProviderError) as raised:
        await conversation.turn([ToolResult("x", "{}", False)])
    assert raised.value.category == "internal"
    await conversation.turn([])
    with pytest.raises(ProviderError) as raised:
        await conversation.turn([])
    assert raised.value.category == "internal"
    assert len(replay.requests) == 1


# --- stops, the turn, usage ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "stop"),
    [
        ("tool_calls", "tool_use"),
        ("end_turn", "end_turn"),
        ("refusal", "refusal"),
        ("content_filter", "refusal"),
        ("max_output_tokens", "max_tokens"),
        ("failed", "other"),
    ],
)
async def test_each_documented_answer_maps_to_its_stop(fixture: str, stop: str) -> None:
    model, replay = _fixture_provider(fixture)
    turn = await model.start(SPEC).turn([])
    assert turn.stop == stop
    assert replay.problems == []


async def test_calls_text_usage_model_and_request_id() -> None:
    model, _ = _fixture_provider("tool_calls")
    turn = await model.start(SPEC).turn([])
    assert [(c.call_id, c.name, c.input) for c in turn.tool_calls] == [
        ("call_fixture_a", "get_music", {}),  # the null strict mode added is dropped
        ("call_fixture_b", "get_music", {"track": 2}),
    ]
    assert turn.text == "Looking at the music first."
    # 9000 prompt tokens: 8000 cached reads, 500 cache writes, 500 plain input
    assert turn.usage == Usage(500, 700, 8000, 500)
    assert turn.model == "gpt-6.1-sol-2026-09-15"
    assert turn.request_id == "req_fixture_tool_calls"


async def test_the_reasoning_and_calls_go_back_unchanged_with_the_results() -> None:
    model, replay = _fixture_provider("tool_calls", "end_turn")
    conversation = model.start(SPEC)
    await conversation.turn([])
    results = [ToolResult("call_fixture_a", '{"ok":true}', False)]
    results.append(ToolResult("call_fixture_b", '{"ok":false}', True))
    await conversation.turn(results, reminder="Call submit_draft.")
    answered = Answer.from_file("openai", "tool_calls", path=PATH).body["output"]
    second = replay.bodies[1]["input"]
    assert second[1:5] == answered  # every output item, as the API returned it
    assert second[5:] == [
        {"type": "function_call_output", "call_id": "call_fixture_a", "output": '{"ok":true}'},
        {"type": "function_call_output", "call_id": "call_fixture_b", "output": '{"ok":false}'},
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Call submit_draft."}],
        },
    ]


async def test_text_is_the_output_text_parts_only() -> None:
    model, _ = _fixture_provider("end_turn")
    assert (await model.start(SPEC).turn([])).text == "Here is my plan. Done."
    model, _ = _fixture_provider("refusal")
    assert (await model.start(SPEC).turn([])).text == ""


@pytest.mark.parametrize(
    ("fixture", "category"),
    [
        ("error_400", "internal"),
        ("error_401", "model_config"),
        ("error_403", "model_config"),
        ("error_404", "model_config"),
        ("error_409", "internal"),
        ("error_422", "internal"),
        ("error_429", "model_unavailable"),
        ("error_500", "model_unavailable"),
        ("error_503", "model_unavailable"),
    ],
)
async def test_every_error_status_maps_and_carries_no_provider_text(
    fixture: str, category: str
) -> None:
    model, _ = _fixture_provider(fixture, max_retries=0)
    with pytest.raises(ProviderError) as raised:
        await model.start(SPEC).turn([])
    assert raised.value.category == category
    assert raised.value.args == (category,)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


async def test_a_server_error_is_retried_by_the_sdk_then_answered() -> None:
    model, replay = _fixture_provider("error_503", "error_429", "end_turn")
    turn = await model.start(SPEC).turn([])
    assert turn.stop == "end_turn"
    assert len(replay.requests) == 3


@pytest.mark.parametrize(
    "spec",
    [
        dataclasses.replace(SPEC, model="gpt-unknown"),
        dataclasses.replace(SPEC, model="claude-opus-5-5"),  # another provider's model
        dataclasses.replace(SPEC, effort="turbo"),
    ],
)
async def test_an_unlisted_model_or_effort_is_model_config_before_any_request(
    spec: StageSpec,
) -> None:
    model, replay = _fixture_provider("end_turn")
    with pytest.raises(ProviderError) as raised:
        await model.start(spec).turn([])
    assert raised.value.category == "model_config"
    assert replay.requests == []


@pytest.mark.parametrize("effort", EFFORTS)
async def test_every_classbeat_effort_is_sent_as_its_openai_effort(effort: str) -> None:
    model, replay = _fixture_provider("end_turn")
    await model.start(dataclasses.replace(SPEC, effort=effort)).turn([])
    assert replay.bodies[0]["reasoning"] == {"effort": effort}


def test_the_provider_repr_shows_no_key() -> None:
    model, _ = _fixture_provider()
    assert TEST_KEY not in repr(model)
    assert TEST_KEY not in repr(model.start(SPEC))


# --- catalog and prices --------------------------------------------------------------------


def test_the_four_openai_models_are_listed_owner_pending() -> None:
    entries = models_of("openai")
    assert [e.model for e in entries] == [
        "gpt-6-astra",
        "gpt-6.1-sol",
        "gpt-6.1-terra",
        "gpt-6-luna",
    ]
    for entry in entries:
        assert (entry.efforts, entry.host, entry.data_policy) == (
            EFFORTS,
            "api.openai.com",
            "owner_pending",
        )
    assert [e for e in CATALOG if e.provider != "anthropic" and e.data_policy == "allowed"] == []


def test_the_prices_version_moved_with_the_openai_prices() -> None:
    assert PRICES_VERSION >= "2026-10-31"


def test_a_cache_write_is_priced_at_its_own_rate() -> None:
    price = DEFAULT_PRICES.lookup("openai", "gpt-6.1-sol")
    assert price is not None
    assert (price.input, price.cache_read, price.cache_write, price.output) == (
        Decimal(2),
        Decimal("0.1"),
        Decimal("2.5"),
        Decimal(10),
    )
    # 500 input * 2 + 8000 read * 0.1 + 500 written * 2.5 + 700 output * 10 micro-dollars
    assert price.cost_micros(Usage(500, 700, 8000, 500)) == 1000 + 800 + 1250 + 7000


async def test_a_long_prompt_is_billed_at_the_long_context_rates() -> None:
    model, _ = _fixture_provider("long_context")
    usage = (await model.start(SPEC).turn([])).usage
    assert usage == Usage(50000, 1000, 200000, 50000)
    assert usage.prompt_tokens > OPENAI_LONG_CONTEXT_TOKENS
    price = DEFAULT_PRICES.lookup("openai", "gpt-6.1-sol")
    assert price is not None
    # long context: input 4, cached 0.2, write 5, output 15
    assert price.cost_micros(usage) == 50000 * 4 + 200000 * 0.2 + 50000 * 5 + 1000 * 15
    at_bound = Usage(OPENAI_LONG_CONTEXT_TOKENS, 0, 0, 0)
    assert price.cost_micros(at_bound) == OPENAI_LONG_CONTEXT_TOKENS * 2  # "at most 272K"


# --- end to end: the planted values and the smoke cases ------------------------------------


async def test_no_planted_value_reaches_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0006 §7 / ADR 0008 §3 against the request bodies the adapter really sent; the
    transport also refuses a planted value in any URL or header (``Replay``)."""
    planted = Planted()
    lib = synthetic_tracks(planted=planted)
    lib.problems = [
        {"Description": planted.unavailable, "TrackInfo": {"TrackName": "Invented Song 7"}}
    ]
    data = analysed(monkeypatch, lib, planted)
    ctx = DesignContext.build(brief(text="Make it hilly"), data)
    fake = from_dict(every_call_script())
    model, api = scripted_openai(fake, planted=list(planted.values()))
    host = MemoryHost()
    await run_stage(host, model, design_stage(ctx), ctx.first_message(), OPENAI)
    assert api.problems == []
    assert len(api.requests) == 3
    raw = [r.content.decode("utf-8") for r in api.requests]
    sent = " ".join([*raw, json.dumps(api.bodies, ensure_ascii=False)])
    assert "Make it hilly" in sent
    assert "Invented Song 0" in sent
    for value in planted.values():
        assert value not in sent, value
    for absent in ("Invented Song 7", "spotify_id", "playlist_id", "unavailable", "Bearer"):
        assert absent not in sent, absent


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c.name)
async def test_the_design_smoke_cases_run_through_openai(case: Any) -> None:
    fake = provider_for_case(case)
    model, api = scripted_openai(fake)
    result = await run_case(case, model, OPENAI)
    assert api.problems == []
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert result.passed
    assert fake.remaining == 0
    assert result.cost_micros > 0


@pytest.mark.parametrize("case", load_pick_cases(), ids=lambda c: c.name)
async def test_the_song_smoke_cases_run_through_openai(case: Any) -> None:
    fake = pick_provider_for_case(case)
    model, api = scripted_openai(fake)
    result, _, _ = await run_pick_case(case, model, OPENAI, (POOL_LIMITS, CHOOSE_LIMITS))
    assert api.problems == []
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert result.passed
    assert fake.remaining == 0
    assert {b["prompt_cache_key"].rsplit("-", 1)[0] for b in api.bodies} == {
        "pick_pool",
        "choose_songs",
    }


def test_the_web_process_never_loads_a_model_sdk_or_adapter() -> None:
    """The key files are the worker's (ADR 0012 §2): the web app imports no adapter and no SDK."""
    code = (
        "import sys, classbeat.web\n"
        "print([m for m in sys.modules if m.split('.')[0] in ('openai', 'anthropic')"
        " or m.startswith('classbeat.agent.providers')])\n"
    )
    done = subprocess.run(  # noqa: S603 - our own interpreter, a fixed probe
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True
    )
    assert done.stdout.strip() == "[]"
