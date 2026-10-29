"""A design stage run on ``AnthropicProvider`` (the fake Messages API of ``anthropicfake``):
usage and cost per turn against the price table and the cost check of ADR 0006 section 5, an
unpriced model, ``provider_for``, and the web process never loading the provider or the SDK."""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import pytest
from pydantic import SecretStr

from agentfake import MemoryHost, analysed, brief, good_draft
from anthropicfake import PLANTED_KEY, FakeAnthropic, Reply, message, stream, text, tool_use
from classbeat.agent import DesignContext, design_stage, run_stage
from classbeat.agent.loop import AgentConfig
from classbeat.agent.prices import DEFAULT_PRICES, Price
from classbeat.agent.provider import Usage
from classbeat.agent.providers import AnthropicProvider, ProviderUnavailable, provider_for
from classbeat.config import Settings
from classbeat.jobs.errors import JobFailure
from egress_scan import imported_modules

SRC = Path(__file__).resolve().parent.parent / "src"
OPUS = DEFAULT_PRICES.lookup("anthropic", "claude-opus-5-5")


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic.AsyncAnthropic, "_calculate_retry_timeout", lambda *_: 0.0)


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> DesignContext:
    return DesignContext.build(brief(), analysed(monkeypatch))


def anthropic_provider(*replies: Reply) -> tuple[AnthropicProvider, FakeAnthropic]:
    fake = FakeAnthropic(list(replies))
    return AnthropicProvider(SecretStr(PLANTED_KEY), transport=fake), fake


async def run(ctx: DesignContext, p: AnthropicProvider, host: MemoryHost, **config: Any) -> Any:
    return await run_stage(host, p, design_stage(ctx), ctx.first_message(), AgentConfig(**config))


def test_the_default_model_is_priced_from_the_published_list() -> None:
    expected = Price(Decimal(4), Decimal(20), Decimal(5), Decimal("0.2"))
    assert DEFAULT_PRICES.lookup("anthropic", "claude-opus-5-5") == expected
    # Nothing else: an unlisted model is not priced by a wildcard.
    assert DEFAULT_PRICES.lookup("anthropic", "claude-sonnet-5-5") is None
    assert DEFAULT_PRICES.lookup("anthropic", "claude-opus-5-5-20990101") is None


def test_opus_cost_arithmetic() -> None:
    assert OPUS is not None
    # $4, $20, $5 and $0.20 per million tokens are micro-dollars per token.
    usage = Usage(input_tokens=1000, output_tokens=200, cache_read_tokens=3, cache_write_tokens=10)
    assert OPUS.cost_micros(usage) == 4000 + 4000 + 50 + 1  # 0.6 rounded up
    assert OPUS.output_micros(8000) == 160_000  # the per-turn reserve: $0.16
    assert OPUS.output_micros(32_000) == 640_000  # all of max_tokens: $0.64 (section 5)
    assert OPUS.prompt_micros(1000) == 5000  # estimates at the dearer cache-write rate


async def test_a_run_records_usage_and_cost_per_turn(ctx: DesignContext) -> None:
    turn1 = message(
        tool_use("toolu_1", "get_music", {}), stop="tool_use", usage=(1000, 200, 0, 3000)
    )
    turn2 = message(
        text("Here is the draft."),
        tool_use("toolu_2", "submit_draft", {"draft": good_draft()}),
        stop="tool_use",
        model="claude-opus-5-5",
        usage=(400, 600, 3000, 900),
    )
    p, fake = anthropic_provider(stream(turn1), stream(turn2))
    host = MemoryHost()
    outcome = await run(ctx, p, host)
    assert host.outcomes() == ["submitted"]
    assert (outcome.turns, outcome.tool_calls) == (2, 2)
    assert outcome.usage == Usage(1400, 800, 3000, 3900)
    first = 1000 * 4 + 200 * 20 + 3000 * 5
    second = 400 * 4 + 600 * 20 + 900 * 5 + 3000 // 5  # 3000 cache reads at $0.20
    assert outcome.cost_micros == first + second == 41_700
    assert outcome.model_answered == "claude-opus-5-5"
    assert outcome.info.provider == "anthropic"
    [record] = host.runs.values()
    assert record["records"] == 2
    assert record["counters"].cost_micros == 41_700
    # The tool results went back in the second request, answered by call id.
    results = fake.bodies[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_1"]
    assert json.loads(results[0]["content"])["ok"] is True


async def test_the_cost_check_stops_before_a_request(ctx: DesignContext) -> None:
    p, fake = anthropic_provider(stream(message(text("never"))))
    host = MemoryHost(spent_before=1_400_000)  # $1.40 of $1.50 spent; the reserve is $0.16
    with pytest.raises(JobFailure) as caught:
        await run(ctx, p, host)
    assert caught.value.category == "limit_exceeded"
    assert fake.requests == []
    assert host.outcomes() == ["limit_exceeded"]


async def test_the_cost_check_stops_a_run_whose_spend_grows(ctx: DesignContext) -> None:
    """Each turn spends about $0.45 (big cache writes): the ceiling stops the third turn."""
    turns = [
        stream(
            message(
                tool_use(f"toolu_{n}", "get_music", {}), stop="tool_use", usage=(0, 0, 0, 90_000)
            )
        )
        for n in range(5)
    ]
    p, fake = anthropic_provider(*turns)
    host = MemoryHost()
    with pytest.raises(JobFailure) as caught:
        await run(ctx, p, host)
    assert caught.value.category == "limit_exceeded"
    assert len(fake.requests) == 2
    [record] = host.runs.values()
    assert record["counters"].cost_micros == 2 * 450_000


async def test_an_unpriced_model_is_model_config_before_any_request(ctx: DesignContext) -> None:
    p, fake = anthropic_provider(stream(message(text("never"))))
    host = MemoryHost()
    with pytest.raises(JobFailure) as caught:
        await run(ctx, p, host, model="claude-sonnet-5-5")
    assert caught.value.category == "model_config"
    assert fake.requests == []


async def test_a_blocked_request_fails_the_run_outbound_blocked(ctx: DesignContext) -> None:
    p, fake = anthropic_provider(Reply(302, b"", {"location": "https://evil.example/"}))
    host = MemoryHost()
    with pytest.raises(JobFailure) as caught:
        await run(ctx, p, host)
    assert caught.value.category == "outbound_blocked"
    assert host.outcomes() == ["outbound_blocked"]
    assert len(fake.requests) == 1  # never retried


def test_provider_for_builds_the_anthropic_provider(kek_dir: Path) -> None:
    (kek_dir / "anthropic_api_key").write_text(PLANTED_KEY, "utf-8")
    built = provider_for(Settings(model_provider="anthropic"))
    assert isinstance(built, AnthropicProvider)
    assert PLANTED_KEY not in repr(built)


def test_provider_for_refuses_anthropic_without_a_key(kek_dir: Path) -> None:
    (kek_dir / "anthropic_api_key").write_text("", "utf-8")
    with pytest.raises(ProviderUnavailable, match="anthropic_api_key"):
        provider_for(Settings(model_provider="anthropic"))


@pytest.mark.parametrize("name", ["ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS"])
def test_provider_for_refuses_sdk_environment(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (kek_dir / "anthropic_api_key").write_text(PLANTED_KEY, "utf-8")
    monkeypatch.setenv(name, "x-api-key: something")
    with pytest.raises(ProviderUnavailable, match=name) as raised:
        provider_for(Settings(model_provider="anthropic"))
    assert "something" not in str(raised.value)


def test_fake_stays_refused_in_prod_with_a_key(kek_dir: Path, tmp_path: Path) -> None:
    (kek_dir / "anthropic_api_key").write_text(PLANTED_KEY, "utf-8")
    script = tmp_path / "s.json"
    script.write_text(json.dumps({"turns": [{"refusal": True}]}), "utf-8")
    settings = Settings(
        model_provider="fake",
        environment="prod",
        fake_model_script=script,
        public_base_url="https://classbeat.example",
    )
    with pytest.raises(ProviderUnavailable, match="prod"):
        provider_for(settings)


def test_the_web_process_never_loads_the_provider_or_the_sdk() -> None:
    probe = (
        "import json, sys\n"
        "import classbeat.__main__, classbeat.web\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run(  # noqa: S603 - our own interpreter, a fixed probe
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, check=True
    )
    loaded = json.loads(result.stdout.splitlines()[-1])
    assert [m for m in loaded if m == "anthropic" or m.startswith("anthropic.")] == []
    assert [m for m in loaded if m.startswith("classbeat.agent")] == []


def test_only_the_provider_module_imports_the_sdk() -> None:
    importers = []
    for path in sorted((SRC / "classbeat").rglob("*.py")):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join(parts)
        names = imported_modules(path.read_text("utf-8"), module)
        if any(n == "anthropic" or n.startswith("anthropic.") for n in names):
            importers.append(module)
    assert importers == ["classbeat.agent.providers.anthropic"]
