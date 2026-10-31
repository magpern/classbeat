"""``DeepSeekProvider`` on Chat Completions (ADR 0012 §3, M3c P3, issue #205), offline.

Two kinds of answer, both under the real one-host allowlist and the test key: the hand-written
fixtures of ``tests/provider_fixtures/deepseek/`` (the documented response shapes, for the stops,
the errors and usage), and ``deepseekfake.ScriptedChat``, which plays the ``FakeProvider`` scripts
through the adapter, so real stages, the smoke cases and the planted-value test run through it and
every request body is checked as sent. DeepSeek stays off by default and ``owner_pending``.
"""

import dataclasses
import io
import itertools
import json
import logging
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from agentfake import (
    CONFIG,
    MemoryHost,
    Planted,
    analysed,
    brief,
    from_dict,
    good_draft,
    synthetic_tracks,
)
from agentfake import provider as script_provider
from classbeat.agent import DesignContext, design_stage, run_stage
from classbeat.agent.catalog import CATALOG, models_of
from classbeat.agent.prices import DEFAULT_PRICES
from classbeat.agent.provider import ProviderError, StageSpec, ToolDef, ToolResult, Usage
from classbeat.agent.providers import (
    DeepSeekProvider,
    ProviderUnavailable,
    deepseek_provider,
)
from classbeat.agent.providers.deepseek import request_tools
from classbeat.config import Settings, worker_model_providers
from classbeat.egress import MODEL_HOSTS, worker_hosts
from classbeat.evals.cases import load_cases
from classbeat.evals.pick import load_pick_cases, run_pick_case
from classbeat.evals.runner import results_json, run_case
from classbeat.log import QUIET_LOGGERS, configure_logging
from deepseekfake import DEEPSEEK_EFFORT, DEEPSEEK_MODEL, PATH, check_request, scripted_deepseek
from pickfake import CHOOSE_LIMITS, POOL_LIMITS
from pickscripts import provider_for_case as pick_provider_for_case
from providerreplay import TEST_KEY, Answer, Replay
from smokescripts import provider_for_case
from test_agent_model_input import every_call_script

DEEPSEEK = dataclasses.replace(CONFIG, model=DEEPSEEK_MODEL, effort=DEEPSEEK_EFFORT)
MARKER = "planted-reasoning-marker-7f3a"
TRACK: dict[str, Any] = {
    "type": "object",
    "properties": {"track": {"type": "integer"}},
    "additionalProperties": False,
}
SPEC = StageSpec(
    stage="design",
    system="You design classes.",
    tools=(ToolDef("get_music", "The music.", TRACK),),
    first_message="A brief.",
    model=DEEPSEEK_MODEL,
    effort="high",
    max_tokens=32000,
)


def _fixture_provider(*names: str, **kw: Any) -> tuple[DeepSeekProvider, Replay]:
    answers = [Answer.from_file("deepseek", n, path=PATH) for n in names]
    replay = Replay("deepseek", answers)
    return DeepSeekProvider(SecretStr(TEST_KEY), transport=replay.transport(), **kw), replay


async def _design_run(monkeypatch: pytest.MonkeyPatch, script: Any = "good_first_draft") -> Any:
    ctx = DesignContext.build(brief(), analysed(monkeypatch))
    fake = script_provider(script) if isinstance(script, str) else from_dict(script)
    model, api = scripted_deepseek(fake, reasoning=MARKER)
    host = MemoryHost()
    outcome = await run_stage(host, model, design_stage(ctx), ctx.first_message(), DEEPSEEK)
    assert api.problems == []
    assert fake.remaining == 0
    return outcome, api, design_stage(ctx), host


# --- the request ---------------------------------------------------------------------------


async def test_the_request_has_exactly_the_decided_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _, api, stage, _ = await _design_run(monkeypatch)
    first = api.bodies[0]
    assert set(first) == {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "max_tokens",
        "reasoning_effort",
        "thinking",
        "stream",
    }
    assert check_request(first) == []
    assert (first["model"], first["reasoning_effort"], first["thinking"]) == (
        DEEPSEEK_MODEL,
        "high",
        {"type": "enabled"},
    )
    assert (first["tool_choice"], first["stream"], first["max_tokens"]) == (
        "auto",
        False,
        DEEPSEEK.max_tokens,
    )
    # non-strict: each tool's own input schema, as the loop validates it (ADR 0012 §3)
    assert first["tools"] == [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": dict(t.input_schema),
            },
        }
        for t in stage.definitions()
    ]
    system, user = first["messages"]
    assert system == {"role": "system", "content": stage.system}
    assert user["role"] == "user"
    assert "Invented" in user["content"]


async def test_the_base_url_is_the_host_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """DeepSeek's documented OpenAI-SDK base URL is ``https://api.deepseek.com`` (no ``/v1``)."""
    _, api, _, _ = await _design_run(monkeypatch)
    assert {str(r.url) for r in api.requests} == {"https://api.deepseek.com/chat/completions"}
    assert MODEL_HOSTS["deepseek"] == "api.deepseek.com"


async def test_two_runs_of_a_stage_send_the_same_system_prompt_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, one, _, _ = await _design_run(monkeypatch)
    _, two, _, _ = await _design_run(monkeypatch)
    fixed = [
        json.dumps([b["messages"][0], b["tools"], b["model"], b["reasoning_effort"]])
        for b in [*one.bodies, *two.bodies]
    ]
    assert len(set(fixed)) == 1
    assert request_tools(SPEC) == request_tools(dataclasses.replace(SPEC, first_message="x"))


# --- the reasoning text: back to DeepSeek only, nowhere else ---------------------------------


@pytest.fixture
def production_logs() -> Iterator[io.StringIO]:
    """The worker's own logging setup (``configure_logging``) at DEBUG, into a buffer; restored
    afterwards."""
    root = logging.getLogger()
    kept = (list(root.handlers), root.level)
    names = (*QUIET_LOGGERS, "sqlalchemy.engine", "sqlalchemy.pool")
    levels = {name: logging.getLogger(name).level for name in names}
    buffer = io.StringIO()
    configure_logging("DEBUG", stream=buffer)
    try:
        yield buffer
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in kept[0]:
            root.addHandler(handler)
        root.setLevel(kept[1])
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


async def test_the_reasoning_goes_back_in_the_next_request_and_nowhere_else(
    monkeypatch: pytest.MonkeyPatch, production_logs: io.StringIO
) -> None:
    """ADR 0012 §3 (amended 2026-10-31): DeepSeek answers 400 when an earlier turn's
    ``reasoning_content`` is missing from a request with tools, so it goes back, from memory
    only: never in a log, a ``Turn``, an event or what the host stores, nor in an eval result."""
    outcome, api, _, host = await _design_run(monkeypatch, every_call_script())
    logging.getLogger("classbeat.test").debug("the run is over")  # the buffer does receive
    assert len(api.bodies) == 3
    # every answer's reasoning is in every later request, as the assistant message it came in
    for n, body in enumerate(api.bodies[1:], start=2):
        assistants = [m for m in body["messages"] if m["role"] == "assistant"]
        assert [m["reasoning_content"] for m in assistants] == [
            f"{MARKER}-{k}" for k in range(1, n)
        ]
    # and nowhere else: logs, what the host would write (runs, counters, events), the outcome
    assert "the run is over" in production_logs.getvalue()
    assert MARKER not in production_logs.getvalue()
    stored = repr([host.infos, host.runs, host.events, outcome])
    assert MARKER not in stored
    assert f"{MARKER}-1" in json.dumps(api.bodies)  # the check can fail


async def test_an_eval_result_holds_no_reasoning() -> None:
    case = load_cases()[0]
    model, api = scripted_deepseek(provider_for_case(case), reasoning=MARKER)
    result = await run_case(case, model, DEEPSEEK)
    assert api.problems == []
    assert result.passed
    assert MARKER in json.dumps(api.bodies[1:])
    assert MARKER not in results_json([result])
    assert MARKER not in repr(result)


async def test_a_turn_carries_the_visible_text_only() -> None:
    model, _ = _fixture_provider("stop")
    turn = await model.start(SPEC).turn([])
    assert turn.text == "Here is my plan."
    assert "fixture-reasoning-marker" not in repr(turn)


async def test_an_answer_without_reasoning_goes_back_without_it() -> None:
    model, replay = _fixture_provider("no_reasoning", "stop")
    conversation = model.start(SPEC)
    await conversation.turn([])
    await conversation.turn([], reminder="Go on.")
    assert replay.bodies[1]["messages"][2] == {"role": "assistant", "content": "ok"}


# --- the history ---------------------------------------------------------------------------


async def test_the_history_is_append_only_and_parallel_calls_are_answered_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ScriptedChat`` refuses any other history (``api.problems``); here, also directly."""
    _, api, _, _ = await _design_run(monkeypatch, every_call_script())
    bodies = api.bodies
    for before, after in itertools.pairwise(bodies):
        assert after["messages"][: len(before["messages"])] == before["messages"]
    third = bodies[2]["messages"][len(bodies[1]["messages"]) :]
    [answer, *tools] = third
    assert answer["role"] == "assistant"
    assert len(answer["tool_calls"]) == 6
    assert [t["tool_call_id"] for t in tools] == [c["id"] for c in answer["tool_calls"]]
    assert {t["role"] for t in tools} == {"tool"}


async def test_the_answer_and_the_results_go_back_as_documented() -> None:
    model, replay = _fixture_provider("tool_calls", "stop")
    conversation = model.start(SPEC)
    turn = await conversation.turn([])
    assert [(c.call_id, c.name, c.input) for c in turn.tool_calls] == [
        ("call_fixture_a", "get_music", {}),
        ("call_fixture_b", "get_music", {"track": 2}),
    ]
    results = [
        ToolResult("call_fixture_a", '{"ok":true}', False),
        ToolResult("call_fixture_b", '{"ok":false}', True),
    ]
    await conversation.turn(results, reminder="Call submit_draft.")
    answered = Answer.from_file("deepseek", "tool_calls", path=PATH).body["choices"][0]["message"]
    assert replay.bodies[1]["messages"][2:] == [
        answered,  # content, reasoning_content and tool_calls, as returned
        {"role": "tool", "tool_call_id": "call_fixture_a", "content": '{"ok":true}'},
        {"role": "tool", "tool_call_id": "call_fixture_b", "content": '{"ok":false}'},
        {"role": "user", "content": "Call submit_draft."},
    ]


async def test_a_failed_turn_appends_nothing() -> None:
    model, replay = _fixture_provider("insufficient_system_resource", "stop")
    conversation = model.start(SPEC)
    with pytest.raises(ProviderError) as raised:
        await conversation.turn([])
    assert raised.value.category == "model_unavailable"
    assert raised.value.__context__ is None
    await conversation.turn([])
    assert replay.bodies[0]["messages"] == replay.bodies[1]["messages"]


async def test_the_first_turn_sends_no_results_and_a_later_one_needs_some() -> None:
    model, replay = _fixture_provider("stop")
    conversation = model.start(SPEC)
    with pytest.raises(ProviderError):
        await conversation.turn([ToolResult("x", "{}", False)])
    await conversation.turn([])
    with pytest.raises(ProviderError) as raised:
        await conversation.turn([])
    assert raised.value.category == "internal"
    assert len(replay.requests) == 1


# --- a malformed call: the loop refuses it --------------------------------------------------


async def test_a_malformed_call_is_passed_on_as_it_came() -> None:
    model, _ = _fixture_provider("malformed_arguments")
    turn = await model.start(SPEC).turn([])
    assert [(c.name, c.input) for c in turn.tool_calls] == [("get_music", '{"track": 2')]


async def test_the_loop_refuses_a_malformed_call_from_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-strict tools: the loop's own validation is what refuses (ADR 0006 §4)."""
    draft = {"draft": {"$draft": "good"}}
    script = {
        "drafts": {"good": good_draft()},
        "turns": [
            {
                "calls": [
                    {
                        "name": "get_music",
                        "input": {"$raw": '{"track": 2'},
                        "expect": {"ok": False, "error": "invalid_input"},
                    },
                    {
                        "name": "get_music",
                        "input": {"track": "two"},
                        "expect": {"ok": False, "error": "invalid_input"},
                    },
                ]
            },
            {"calls": [{"name": "submit_draft", "input": draft}]},
        ],
    }
    outcome, _, _, host = await _design_run(monkeypatch, script)
    assert ("get_music", "invalid_input") in host.events
    assert outcome.output is not None  # then it submitted the good draft


# --- stops, usage, errors -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "stop"),
    [
        ("tool_calls", "tool_use"),
        ("stop", "end_turn"),
        ("length", "max_tokens"),
        ("content_filter", "refusal"),
        ("aborted", "other"),
    ],
)
async def test_each_finish_reason_maps_to_its_stop(fixture: str, stop: str) -> None:
    model, replay = _fixture_provider(fixture)
    assert (await model.start(SPEC).turn([])).stop == stop
    assert replay.problems == []


async def test_usage_model_and_request_id() -> None:
    model, _ = _fixture_provider("tool_calls")
    turn = await model.start(SPEC).turn([])
    assert turn.usage == Usage(1000, 700, 8000, 0)  # 9000 prompt: 8000 cache hits, no write
    assert turn.model == "deepseek-flash"
    assert turn.request_id == "req_fixture-tool-calls"


@pytest.mark.parametrize(
    ("fixture", "category"),
    [
        ("error_400", "internal"),
        ("error_401", "model_config"),
        ("error_402", "model_config"),  # Insufficient Balance: the operator's to fix
        ("error_422", "internal"),
        ("error_429", "model_unavailable"),
        ("error_500", "model_unavailable"),
        ("error_503", "model_unavailable"),
    ],
)
async def test_every_documented_error_maps_and_carries_no_provider_text(
    fixture: str, category: str
) -> None:
    model, _ = _fixture_provider(fixture, max_retries=0)
    with pytest.raises(ProviderError) as raised:
        await model.start(SPEC).turn([])
    assert raised.value.args == (category,)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "spec",
    [
        dataclasses.replace(SPEC, model="deepseek-unknown"),
        dataclasses.replace(SPEC, model="gpt-6.1-sol"),  # another provider's model
        dataclasses.replace(SPEC, effort="medium"),  # DeepSeek would turn it into high
        dataclasses.replace(SPEC, effort="xhigh"),
    ],
)
async def test_an_unlisted_model_or_effort_is_model_config_before_any_request(
    spec: StageSpec,
) -> None:
    model, replay = _fixture_provider("stop")
    with pytest.raises(ProviderError) as raised:
        await model.start(spec).turn([])
    assert raised.value.category == "model_config"
    assert replay.requests == []


@pytest.mark.parametrize("effort", ["low", "high", "max"])
async def test_each_listed_effort_is_sent_as_it_is(effort: str) -> None:
    model, replay = _fixture_provider("stop")
    await model.start(dataclasses.replace(SPEC, effort=effort)).turn([])
    assert replay.bodies[0]["reasoning_effort"] == effort


def test_the_provider_repr_shows_no_key() -> None:
    model, _ = _fixture_provider()
    assert TEST_KEY not in repr(model)
    assert TEST_KEY not in repr(model.start(SPEC))


# --- off by default --------------------------------------------------------------------------


def _key(kek_dir: Path) -> None:
    (kek_dir / "deepseek_api_key").write_text(TEST_KEY, encoding="utf-8")


def test_the_factory_refuses_while_deepseek_is_off(kek_dir: Path) -> None:
    _key(kek_dir)
    settings = Settings()
    assert settings.deepseek_enabled is False
    with pytest.raises(ProviderUnavailable, match="DeepSeek is off") as raised:
        deepseek_provider(settings)
    assert TEST_KEY not in str(raised.value)
    assert MODEL_HOSTS["deepseek"] not in worker_hosts(worker_model_providers(settings))


def test_the_factory_needs_the_key_file_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_DEEPSEEK_ENABLED", "true")
    settings = Settings()
    with pytest.raises(ProviderUnavailable, match="deepseek_api_key"):
        deepseek_provider(settings)
    assert MODEL_HOSTS["deepseek"] not in worker_hosts(worker_model_providers(settings))


async def test_switched_on_with_a_key_it_builds_and_the_host_is_allowed(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key(kek_dir)
    monkeypatch.setenv("CLASSBEAT_DEEPSEEK_ENABLED", "true")
    settings = Settings()
    replay = Replay("deepseek", [Answer.from_file("deepseek", "stop", path=PATH)])
    model = deepseek_provider(settings, transport=replay.transport())
    assert isinstance(model, DeepSeekProvider)
    assert (await model.start(SPEC).turn([])).stop == "end_turn"
    assert replay.problems == []
    assert MODEL_HOSTS["deepseek"] in worker_hosts(worker_model_providers(settings))


# --- catalog and prices --------------------------------------------------------------------


def test_the_two_deepseek_models_are_listed_owner_pending_with_three_efforts() -> None:
    entries = models_of("deepseek")
    assert [e.model for e in entries] == ["deepseek-flash", "deepseek-v4-pro"]
    for entry in entries:
        assert (entry.efforts, entry.host, entry.data_policy) == (
            ("low", "high", "max"),
            "api.deepseek.com",
            "owner_pending",
        )
    assert [e for e in CATALOG if e.provider != "anthropic" and e.data_policy == "allowed"] == []


@pytest.mark.parametrize(
    ("model", "rates"),
    [
        ("deepseek-flash", ("0.3", "0.006", "1.2")),
        ("deepseek-v4-pro", ("1.32", "0.044", "3.96")),
    ],
)
def test_the_prices_are_the_peak_rates_with_no_write_charge(
    model: str, rates: tuple[str, str, str]
) -> None:
    price = DEFAULT_PRICES.lookup("deepseek", model)
    assert price is not None
    assert (price.input, price.cache_read, price.output) == tuple(Decimal(r) for r in rates)
    assert price.cache_write == price.input
    assert price.above is None


# --- end to end: the planted values and the smoke cases ------------------------------------


async def test_no_planted_value_reaches_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR 0006 §7 against the request bodies the adapter really sent; the transport also
    refuses a planted value in any URL or header (``Replay``)."""
    planted = Planted()
    lib = synthetic_tracks(planted=planted)
    lib.problems = [
        {"Description": planted.unavailable, "TrackInfo": {"TrackName": "Invented Song 7"}}
    ]
    data = analysed(monkeypatch, lib, planted)
    ctx = DesignContext.build(brief(text="Make it hilly"), data)
    fake = from_dict(every_call_script())
    model, api = scripted_deepseek(fake, planted=list(planted.values()))
    await run_stage(MemoryHost(), model, design_stage(ctx), ctx.first_message(), DEEPSEEK)
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
async def test_the_design_smoke_cases_run_through_deepseek(case: Any) -> None:
    fake = provider_for_case(case)
    model, api = scripted_deepseek(fake)
    result = await run_case(case, model, DEEPSEEK)
    assert api.problems == []
    assert result.outcome == "submitted"
    assert result.failed_checks == [], [c.detail for c in result.checks if not c.ok]
    assert result.passed
    assert fake.remaining == 0
    assert result.cost_micros > 0


@pytest.mark.parametrize("case", load_pick_cases(), ids=lambda c: c.name)
async def test_the_song_smoke_cases_run_through_deepseek(case: Any) -> None:
    fake = pick_provider_for_case(case)
    model, api = scripted_deepseek(fake)
    result, _, _ = await run_pick_case(case, model, DEEPSEEK, (POOL_LIMITS, CHOOSE_LIMITS))
    assert api.problems == []
    assert result.outcome == "submitted"
    assert result.passed
    assert fake.remaining == 0
