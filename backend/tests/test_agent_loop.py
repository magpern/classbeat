"""The stage runner (ADR 0006 sections 1 and 5) over the FakeProvider and an in-memory host:
the adversarial scripts of section 8, the limits, the reminder rule and provider failures."""

import asyncio
import json
from typing import Any

import pytest

from agentfake import CONFIG, MemoryHost, analysed, brief, from_dict, good_draft, provider
from classbeat.agent import DesignContext, DesignResult, design_stage, run_stage
from classbeat.agent.limits import Limits, tokens_for
from classbeat.agent.loop import AgentConfig, _spec_chars  # pyright: ignore[reportPrivateUsage]
from classbeat.agent.prices import DEFAULT_PRICES, FAKE_PRICE
from classbeat.agent.provider import ProviderError, Usage
from classbeat.agent.providers.fake import FakeProvider, ScriptMismatch
from classbeat.jobs.errors import JobFailure, LeaseLost, RunStopped


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> DesignContext:
    return DesignContext.build(brief(), analysed(monkeypatch))


async def run(
    ctx: DesignContext, fake: FakeProvider, host: MemoryHost | None = None, **config: Any
) -> Any:
    host = host if host is not None else MemoryHost()
    cfg = AgentConfig(**{"model": "fake-model", **config})
    return await run_stage(host, fake, design_stage(ctx), ctx.first_message(), cfg)


async def failure(ctx: DesignContext, fake: FakeProvider, host: MemoryHost, **config: Any) -> str:
    with pytest.raises(JobFailure) as caught:
        await run(ctx, fake, host, **config)
    return caught.value.category


@pytest.mark.parametrize(
    ("name", "turns", "tool_calls"),
    [
        ("good_first_draft", 3, 3),
        ("fixed_after_validate", 2, 2),
        ("unknown_tool", 2, 2),
        ("out_of_range_index", 3, 5),
        ("oversized_input", 2, 3),
        ("reminded_then_submitted", 2, 1),
    ],
)
async def test_scripts_that_end_with_a_draft(
    ctx: DesignContext, name: str, turns: int, tool_calls: int
) -> None:
    fake, host = provider(name), MemoryHost()
    outcome = await run(ctx, fake, host)
    assert fake.remaining == 0
    assert isinstance(outcome.output, DesignResult)
    assert (outcome.turns, outcome.tool_calls) == (turns, tool_calls)
    assert host.outcomes() == ["submitted"]
    [record] = host.runs.values()
    assert record["records"] == turns  # counters written after every turn
    assert outcome.model_answered == "fake"
    assert outcome.info.provider == "fake"
    assert outcome.info.prompt_version == design_stage(ctx).prompt_version
    result: DesignResult = outcome.output
    assert result.duration_sec == 1800
    assert result.spec["segments"][-1]["kind"] == "cooldown"
    assert all(isinstance(i["rpm"], list) for s in result.spec["segments"] for i in s["intervals"])


async def test_a_draft_fixed_after_validate_errors_got_the_library_message(
    ctx: DesignContext,
) -> None:
    fake = provider("fixed_after_validate")
    await run(ctx, fake)
    [(results, _)] = fake.sent[0].turns[1:]
    [refused] = results
    body = json.loads(refused.content)
    assert refused.is_error
    assert body == {
        "ok": False,
        "error": "invalid_draft",
        "messages": ["the last segment must be the cooldown"],
    }


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("missing_final_call", "invalid_output"),
        ("refusal", "model_refused"),
        ("turn_limit", "limit_exceeded"),
        ("cost_limit", "limit_exceeded"),
        ("corrections_limit", "invalid_output"),
    ],
)
async def test_scripts_that_end_without_a_draft(
    ctx: DesignContext, name: str, category: str
) -> None:
    host = MemoryHost()
    assert await failure(ctx, provider(name), host) == category
    assert host.outcomes() == [category]


async def test_a_missing_final_call_gets_one_reminder_that_counts_as_a_turn(
    ctx: DesignContext,
) -> None:
    fake, host = provider("missing_final_call"), MemoryHost()
    await failure(ctx, fake, host)
    sent = fake.sent[0].turns
    assert [reminder for _, reminder in sent] == [
        None,
        "Finish by calling submit_draft with the final draft.",
    ]
    assert [results for results, _ in sent] == [(), ()]
    [record] = host.runs.values()
    assert record["counters"].turns == 2


async def test_the_turn_limit_stops_before_the_thirteenth_turn(ctx: DesignContext) -> None:
    fake, host = provider("turn_limit"), MemoryHost()
    await failure(ctx, fake, host)
    assert fake.remaining == 1  # the 13th turn was never requested
    [record] = host.runs.values()
    assert record["counters"].turns == 12


async def test_the_cost_ceiling_wins_over_the_turn_limit_after_six_to_eight_turns(
    ctx: DesignContext,
) -> None:
    fake, host = provider("cost_limit"), MemoryHost()
    await failure(ctx, fake, host)
    [record] = host.runs.values()
    counters = record["counters"]
    assert 6 <= counters.turns <= 8
    limits = Limits()
    assert counters.cost_micros <= limits.job_cost_micros  # stopped before it was exceeded
    assert counters.cost_micros == FAKE_PRICE.cost_micros(counters.usage)


async def test_the_cost_check_counts_what_the_job_spent_in_earlier_runs(
    ctx: DesignContext,
) -> None:
    # 1.40 of 1.50 spent already: the reserve alone (8000 output tokens at $20/M) passes it.
    host = MemoryHost(spent_before=1_400_000)
    fake = provider("good_first_draft")
    assert await failure(ctx, fake, host) == "limit_exceeded"
    assert fake.remaining == 3  # no turn was requested
    host = MemoryHost(spent_before=1_000_000)
    await run(ctx, provider("good_first_draft"), host)
    assert host.outcomes() == ["submitted"]


def test_the_reserve_arithmetic_matches_the_adr() -> None:
    # ADR 0006 section 5: 32 000 tokens of output are about $0.64; with an 8 000 reserve a turn
    # can overshoot the ceiling by at most (32 000 - 8 000) tokens, about $0.48.
    assert FAKE_PRICE.output_micros(32_000) == 640_000
    assert FAKE_PRICE.output_micros(32_000 - Limits().turn_output_reserve) == 480_000
    assert FAKE_PRICE.cost_micros(Usage(1_000_000, 0, 0, 0)) == 4_000_000
    assert FAKE_PRICE.cost_micros(Usage(1, 1, 1, 1)) == 30  # rounded up to whole micro-dollars


async def test_overload_then_success_fails_retryably_and_the_next_run_continues(
    ctx: DesignContext,
) -> None:
    fake, host = provider("overload_then_success"), MemoryHost()
    assert await failure(ctx, fake, host) == "model_unavailable"
    outcome = await run(ctx, fake, host)  # the job's next attempt
    assert isinstance(outcome.output, DesignResult)
    assert host.outcomes() == ["model_unavailable", "submitted"]
    assert fake.remaining == 0


@pytest.mark.parametrize(
    ("error", "category"),
    [
        ("overloaded", "model_unavailable"),
        ("timeout", "model_unavailable"),
        ("rate_limited", "model_unavailable"),
        ("auth", "model_config"),
        ("not_found", "model_config"),
        ("bad_request", "internal"),
    ],
)
async def test_provider_errors_end_the_run_with_their_category(
    ctx: DesignContext, error: str, category: str
) -> None:
    host = MemoryHost()
    assert await failure(ctx, from_dict({"turns": [{"error": error}]}), host) == category
    assert host.outcomes() == [category]


@pytest.mark.parametrize("stop", ["max_tokens", "other"])
async def test_max_tokens_and_other_stops_are_invalid_output(ctx: DesignContext, stop: str) -> None:
    host = MemoryHost()
    assert await failure(ctx, from_dict({"turns": [{stop: True}]}), host) == "invalid_output"


async def test_the_tool_call_limit_counts_every_call_including_refused_ones(
    ctx: DesignContext,
) -> None:
    calls = [{"name": "nope", "input": {}} for _ in range(5)]
    fake = from_dict({"turns": [{"calls": calls}]})
    host = MemoryHost()
    limits = Limits(max_tool_calls=3)
    assert await failure(ctx, fake, host, limits=limits) == "limit_exceeded"
    assert host.events == [("unknown", "unknown_tool")] * 3


async def test_the_wall_time_limit_is_checked_before_each_turn(
    ctx: DesignContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]

    def clock() -> float:
        now[0] += 200.0
        return now[0]

    fake = provider("good_first_draft")
    host = MemoryHost()
    with pytest.raises(JobFailure) as caught:
        await run_stage(host, fake, design_stage(ctx), ctx.first_message(), CONFIG, clock=clock)
    assert caught.value.category == "limit_exceeded"
    assert fake.remaining == 2  # one turn ran, then 400 s had passed


async def test_a_model_the_price_table_does_not_know_is_model_config(
    ctx: DesignContext,
) -> None:
    fake = provider("good_first_draft")
    fake.name = "unpriced"
    host = MemoryHost()
    assert await failure(ctx, fake, host, prices=DEFAULT_PRICES) == "model_config"
    assert fake.remaining == 3


async def test_parallel_calls_run_in_order_and_are_answered_together(ctx: DesignContext) -> None:
    fake = from_dict(
        {
            "drafts": {"good": good_draft()},
            "turns": [
                {
                    "calls": [
                        {"name": "get_music", "input": {"track": 0}},
                        {"name": "preview_draft", "input": {"draft": {"$draft": "good"}}},
                        {"name": "get_music", "input": {}},
                    ]
                },
                {"calls": [{"name": "submit_draft", "input": {"draft": {"$draft": "good"}}}]},
            ],
        }
    )
    host = MemoryHost()
    await run(ctx, fake, host)
    results, _ = fake.sent[0].turns[1]
    assert [r.call_id for r in results] == ["call_1_0", "call_1_1", "call_1_2"]
    assert [json.loads(r.content).get("track", {}).get("index") for r in results] == [0, None, None]
    assert host.events == [
        ("get_music", None),
        ("preview_draft", None),
        ("get_music", None),
        ("submit_draft", None),
    ]


async def test_a_successful_final_call_ends_the_turn(ctx: DesignContext) -> None:
    draft = {"draft": {"$draft": "good"}}
    fake = from_dict(
        {
            "drafts": {"good": good_draft()},
            "turns": [
                {
                    "calls": [
                        {"name": "submit_draft", "input": draft},
                        {"name": "save_workout", "input": {}},
                    ]
                }
            ],
        }
    )
    host = MemoryHost()
    await run(ctx, fake, host)
    assert host.events == [("submit_draft", None)]


async def test_a_cancel_at_a_checkpoint_records_cancelled_and_stops(ctx: DesignContext) -> None:
    fake = provider("good_first_draft")
    host = MemoryHost(stop_at_checkpoint=3, stop_with=lambda: RunStopped("cancelled"))
    with pytest.raises(RunStopped):
        await run(ctx, fake, host)
    assert host.outcomes() == ["cancelled"]
    assert fake.remaining == 2  # one turn and its tool call, then the cancel


@pytest.mark.parametrize("reason", ["shutdown", "lost"])
async def test_shutdown_and_a_lost_lease_record_no_outcome(ctx: DesignContext, reason: str) -> None:
    host = MemoryHost(stop_at_checkpoint=2, stop_with=lambda: RunStopped(reason))
    with pytest.raises(RunStopped):
        await run(ctx, provider("good_first_draft"), host)
    assert host.outcomes() == [None]


async def test_lease_lost_propagates_without_a_write(ctx: DesignContext) -> None:
    host = MemoryHost(stop_at_checkpoint=2, stop_with=LeaseLost)
    with pytest.raises(LeaseLost):
        await run(ctx, provider("good_first_draft"), host)
    assert host.outcomes() == [None]


async def test_a_script_mismatch_fails_loudly(ctx: DesignContext) -> None:
    fake = from_dict(
        {
            "turns": [
                {"calls": [{"name": "get_music", "input": {}, "expect": {"ok": False}}]},
                {"end_turn": "x"},
            ]
        }
    )
    host = MemoryHost()
    with pytest.raises(ScriptMismatch):
        await run(ctx, fake, host)
    assert host.outcomes() == ["internal"]


async def test_a_script_that_runs_out_fails_loudly(ctx: DesignContext) -> None:
    fake = from_dict({"turns": [{"calls": [{"name": "get_music", "input": {}}]}]})
    with pytest.raises(ScriptMismatch):
        await run(ctx, fake)


async def test_runs_of_two_users_send_identical_system_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one = DesignContext.build(brief(text="first user"), analysed(monkeypatch))
    two = DesignContext.build(brief(text="second", climbs=3), analysed(monkeypatch, name="Other"))
    sent = []
    for ctx in (one, two):
        fake = provider("good_first_draft")
        await run(ctx, fake)
        sent.append(fake.sent[0].spec)
    assert sent[0].system == sent[1].system
    assert sent[0].tools == sent[1].tools
    assert sent[0].first_message != sent[1].first_message


async def test_an_abandoned_run_writes_nothing_more(ctx: DesignContext) -> None:
    class Slow(FakeProvider):
        def next_turn(self, results: Any) -> Any:
            raise AssertionError("not reached")

    class SlowConversation:
        async def turn(self, results: Any, *, reminder: str | None = None) -> Any:
            await asyncio.sleep(10)
            raise AssertionError("not reached")

    fake = Slow(provider("refusal").script)
    fake.start = lambda spec: SlowConversation()  # type: ignore[method-assign]
    host = MemoryHost()
    task = asyncio.ensure_future(run(ctx, fake, host))
    await asyncio.sleep(0.05)
    task.cancel()  # what the job runner does when the lease is lost
    with pytest.raises(asyncio.CancelledError):
        await task
    assert host.outcomes() == [None]


class _Unreadable:
    """A provider whose first answer cannot be read: ``billed`` says whether it was answered."""

    name = "fake"  # FAKE_PRICE

    def __init__(self, *, billed: bool) -> None:
        self.billed = billed
        self.specs: list[Any] = []

    def start(self, spec: Any) -> "_Unreadable":
        self.specs.append(spec)
        return self

    async def turn(self, results: Any, *, reminder: str | None = None) -> Any:
        raise ProviderError("internal", billed=self.billed)


async def test_a_billed_turn_that_cannot_be_read_is_charged_at_its_ceiling(
    ctx: DesignContext,
) -> None:
    """ADR 0006 §5, #299 item 2: the prompt estimate at the dearest input rate plus max_tokens of
    output, as one turn, recorded before the run fails; never 0."""
    fake, host = _Unreadable(billed=True), MemoryHost()
    assert await failure(ctx, fake, host, max_tokens=32_000) == "internal"  # pyright: ignore[reportArgumentType]
    [record] = host.runs.values()
    counters = record["counters"]
    [spec] = fake.specs
    prompt = FAKE_PRICE.prompt_micros(tokens_for(_spec_chars(spec)))  # pyright: ignore[reportPrivateUsage]
    assert counters.cost_micros == prompt + FAKE_PRICE.output_micros(32_000) > 640_000
    assert (counters.turns, record["records"]) == (1, 1)
    assert host.outcomes() == ["internal"]


async def test_an_unanswered_request_costs_nothing(ctx: DesignContext) -> None:
    fake, host = _Unreadable(billed=False), MemoryHost()
    assert await failure(ctx, fake, host) == "internal"  # pyright: ignore[reportArgumentType]
    [record] = host.runs.values()
    assert record["records"] == 0
    assert (record["counters"].turns, record["counters"].cost_micros) == (0, 0)
