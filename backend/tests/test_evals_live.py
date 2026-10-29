"""The live command of the smoke set (ADR 0006 section 8, ADR 0010): it refuses unless every
condition holds, prints its cost ceiling before running, stays inside ADR 0006 section 5's limits
and never prints the key. Nothing here contacts a provider: the one full run goes through the real
``AnthropicProvider`` and the real allowlist transport, answered by ``FakeAnthropic``."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import anthropic
import pytest

from agentfake import from_dict
from anthropicfake import (
    PLANTED_KEY,
    FakeAnthropic,
    message,
    stream,
    tool_use,
)
from classbeat.agent.loop import AgentConfig
from classbeat.agent.provider import StageSpec, ToolCall, Turn, Usage
from classbeat.agent.providers import AnthropicProvider
from classbeat.agent.providers.fake import FakeProvider
from classbeat.config import Settings
from classbeat.evals import live
from classbeat.evals.cases import load_cases
from classbeat.evals.live import main_live, plan_text
from smokescripts import good_draft

CASES = {c.name: c for c in load_cases()}


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anthropic.AsyncAnthropic, "_calculate_retry_timeout", lambda *_: 0.0)


@pytest.fixture(autouse=True)
def not_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command refuses when CI is set; the tests that exercise the rest start without it."""
    monkeypatch.delenv("CI", raising=False)


@pytest.fixture
def key(kek_dir: Path) -> None:
    (kek_dir / "anthropic_api_key").write_text(PLANTED_KEY, "utf-8")


class Spy:
    def __init__(self, fake: FakeAnthropic | None = None) -> None:
        self.fake = fake
        self.installed = 0
        self.built = 0

    def install(self) -> None:
        self.installed += 1

    def provider(self, settings: Settings) -> AnthropicProvider:
        self.built += 1
        assert settings.anthropic_api_key is not None
        assert self.fake is not None, "a test that reaches the provider must bring its fake"
        return AnthropicProvider(settings.anthropic_api_key, transport=self.fake)


def run(
    spy: Spy,
    settings: Settings | None = None,
    *,
    live: bool = True,
    max_usd: str | None = "5",
    cases: list[str] | None = None,
    out_path: Path | None = None,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main_live(
        settings or Settings(model_provider="anthropic"),
        live=live,
        max_usd=max_usd,
        case_names=cases,
        out_path=out_path,
        out=out,
        err=err,
        provider_factory=spy.provider,
        install=spy.install,
    )
    return code, out.getvalue(), err.getvalue()


def nothing_ran(spy: Spy) -> bool:
    return spy.installed == 0 and spy.built == 0


@pytest.mark.usefixtures("key")
def test_it_refuses_without_the_live_flag() -> None:
    spy = Spy()
    code, out, err = run(spy, live=False)
    assert code == 2
    assert "--live" in err
    assert out == ""
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_it_refuses_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    spy = Spy()
    code, _, err = run(spy)
    assert code == 2
    assert "CI" in err
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_it_refuses_the_fake_provider() -> None:
    spy = Spy()
    code, out, err = run(spy, Settings(model_provider="fake"))
    assert code == 2
    assert "not anthropic" in err
    assert out == ""
    assert nothing_ran(spy)


def test_it_refuses_without_a_key(kek_dir: Path) -> None:
    (kek_dir / "anthropic_api_key").write_text("", "utf-8")
    spy = Spy()
    code, _, err = run(spy)
    assert code == 2
    assert "anthropic_api_key" in err
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
@pytest.mark.parametrize("name", ["ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS"])
def test_it_refuses_the_sdk_environment(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "x-api-key: something")
    spy = Spy()
    code, out, err = run(spy)
    assert code == 2
    assert name in err
    assert "something" not in out + err
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_it_prints_the_cost_ceiling_and_the_limits_then_stops_without_max_usd() -> None:
    spy = Spy()
    code, out, err = run(spy, max_usd=None)
    assert code == 2
    assert "--max-usd" in err
    assert nothing_ran(spy)
    assert "15 cases" in out
    assert "claude-opus-5-5" in out
    # 15 cases x ($1.50 job ceiling + (32000 - 8000) tokens of output at $20 per million).
    assert "estimated cost ceiling for this run: $29.70" in out
    assert "12 turns, 40 tool calls, 3 corrections, 300 s, $1.50 job ceiling" in out
    assert PLANTED_KEY not in out + err


def test_a_lower_budget_lowers_the_ceiling() -> None:
    config = AgentConfig.from_settings(Settings(model_provider="fake"))
    text = plan_text(config, list(CASES.values()), 5_000_000)
    assert "estimated cost ceiling for this run: $5.48" in text  # $5 plus one turn's over-run
    assert "the budget is $5.00" in text


@pytest.mark.usefixtures("key")
@pytest.mark.parametrize("amount", ["abc", "0", "-1", "100.01", "nan"])
def test_it_refuses_a_bad_budget(amount: str) -> None:
    spy = Spy()
    code, _, err = run(spy, max_usd=amount)
    assert code == 2
    assert "--max-usd" in err
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_it_refuses_an_unknown_case() -> None:
    spy = Spy()
    code, _, err = run(spy, cases=["nope"])
    assert code == 2
    assert "unknown case" in err
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
async def test_a_full_run_goes_through_the_real_provider_and_prints_no_secret(
    tmp_path: Path,
) -> None:
    case = CASES["minimum_10"]
    replies = [
        stream(
            message(tool_use("toolu_1", "get_music", {}), stop="tool_use", usage=(4000, 100, 0, 0))
        ),
        stream(
            message(
                tool_use("toolu_2", "submit_draft", {"draft": good_draft(case)}),
                stop="tool_use",
                usage=(5000, 2000, 0, 0),
            )
        ),
    ]
    spy = Spy(FakeAnthropic(replies))
    results = tmp_path / "results.json"
    code, out, err = await _in_thread(run, spy, cases=["minimum_10"], out_path=results)
    assert code == 0, (out, err)
    assert spy.installed == 1
    assert len(spy.fake.requests if spy.fake else []) == 2
    assert "minimum_10: pass (submitted)" in out
    assert "1 of 1 passed" in out
    body = results.read_text("utf-8")
    data = json.loads(body)["results"][0]
    assert data["passed"]
    assert data["cost_micros"] == 4000 * 4 + 100 * 20 + 5000 * 4 + 2000 * 20
    assert data["model_answered"] == "claude-opus-5-5"
    for text in (out, err, body):
        assert PLANTED_KEY not in text
        assert "sk-ant" not in text
        assert "Smoke Song" not in text


@pytest.mark.usefixtures("key")
async def test_the_budget_stops_a_case_before_any_request() -> None:
    fake = FakeAnthropic([stream(message(tool_use("t", "get_music", {}), stop="tool_use"))])
    spy = Spy(fake)
    # $0.0001 left: the cost check (input estimate plus the output reserve) refuses turn one.
    code, out, _ = await _in_thread(run, spy, max_usd="0.0001", cases=["easy_30", "minimum_10"])
    assert code == 1
    assert fake.requests == []
    assert "easy_30: FAIL (limit_exceeded)" in out
    assert "minimum_10: FAIL (limit_exceeded)" in out


@pytest.mark.usefixtures("key")
async def test_a_provider_failure_prints_the_category_only() -> None:
    from anthropicfake import status

    fake = FakeAnthropic([status(401, "authentication_error", f"bad key {PLANTED_KEY}")])
    spy = Spy(fake)
    code, out, err = await _in_thread(run, spy, cases=["easy_30"])
    assert code == 1
    assert "easy_30: FAIL (model_config)" in out
    assert PLANTED_KEY not in out + err
    assert "bad key" not in out + err


async def _in_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """``main_live`` runs its own event loop, so call it off the test's loop."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


def test_the_module_refuses_without_live_when_run_as_a_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "classbeat.evals", "smoke"],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "CLASSBEAT_SECRETS_DIR": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 2
    assert "--live" in result.stderr


@pytest.mark.usefixtures("key")
def test_it_refuses_when_ci_is_set_even_if_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "")
    spy = Spy()
    code, _, err = run(spy)
    assert code == 2
    assert "CI" in err
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_it_refuses_an_unpriced_model_before_installing_or_building_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLASSBEAT_AGENT_MODEL", "claude-unpriced-1")
    spy = Spy()
    code, out, err = run(spy)
    assert code == 2
    assert "no price" in err
    assert out == ""
    assert nothing_ran(spy)


def _script(cost_input: int) -> FakeProvider:
    """get_music, then a refusal: one turn of ``cost_input`` input tokens at $4 per million."""
    return from_dict(
        {
            "turns": [
                {
                    "calls": [{"name": "get_music", "input": {}}],
                    "usage": {"input": cost_input, "output": 0},
                },
                {"refusal": True},
            ]
        }
    )


class Exploding:
    """A provider whose second turn raises something that is not a ``ProviderError``."""

    name = "fake"

    def start(self, spec: StageSpec) -> "Exploding":
        self.turns = 0
        return self

    async def turn(self, results: Any, *, reminder: str | None = None) -> Turn:
        self.turns += 1
        if self.turns > 1:
            raise RuntimeError("planted detail that must not be printed")
        call = ToolCall("c1", "get_music", {})
        return Turn("tool_use", "", (call,), Usage(input_tokens=50_000), "fake-model")


def _watch_ceilings(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []
    real = live.run_case

    async def spy(case: Any, provider: Any, config: Any) -> Any:
        seen.append(config.limits.job_cost_micros)
        return await real(case, provider, config)

    monkeypatch.setattr(live, "run_case", spy)
    return seen


async def test_the_budget_is_a_running_total_across_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _watch_ceilings(monkeypatch)
    out = io.StringIO()
    cases = [CASES[n] for n in ("easy_30", "default_30", "minimum_10")]
    # Each case spends $0.60 (150 000 input tokens); the budget is $1.00.
    results = await live.run_live(
        cases, lambda: _script(150_000), AgentConfig(model="fake-model"), 1_000_000, out
    )
    assert seen == [1_000_000, 400_000]  # the second case gets what is left
    assert [r.cost_micros for r in results] == [600_000, 600_000]
    assert "budget used up; minimum_10 and the rest not run" in out.getvalue()


async def test_a_case_that_raises_still_counts_what_it_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _watch_ceilings(monkeypatch)
    out = io.StringIO()
    providers = iter([Exploding(), _script(10_000)])
    results = await live.run_live(
        [CASES["easy_30"], CASES["default_30"]],
        lambda: next(providers),
        AgentConfig(model="fake-model"),
        1_000_000,
        out,
    )
    assert results[0].outcome == "error:RuntimeError"
    assert results[0].cost_micros == 200_000  # 50 000 input tokens at $4 per million
    assert seen == [1_000_000, 800_000]
    assert "planted detail" not in out.getvalue()
