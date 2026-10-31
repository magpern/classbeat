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
    Reply,
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
from classbeat.evals.checks import Check
from classbeat.evals.live import main_live, plan_text
from classbeat.evals.runner import CaseResult
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
    model: str | None = None,
    effort: str | None = None,
    repeat: int = 1,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main_live(
        settings or Settings(model_provider="anthropic"),
        live=live,
        max_usd=max_usd,
        case_names=cases,
        out_path=out_path,
        model=model,
        effort=effort,
        repeat=repeat,
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
@pytest.mark.parametrize("name", ["ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS", "SSLKEYLOGFILE"])
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
    assert "18 cases" in out
    assert "claude-opus-5-5" in out
    # 18 cases x ($1.50 job ceiling + (32000 - 8000) tokens of output at $20 per million).
    assert "estimated cost ceiling for this run: $35.64" in out
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

    async def spy(case: Any, provider: Any, config: Any, **kwargs: Any) -> Any:
        seen.append(config.limits.job_cost_micros)
        return await real(case, provider, config, **kwargs)

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


# --- --model, --effort, --repeat (issue #134) ----------------------------------------------------


@pytest.mark.usefixtures("key")
@pytest.mark.parametrize(
    ("model", "ceiling"),
    [("claude-sonnet-5-5", "$31.32"), ("claude-haiku-5-5", "$28.08")],
)
def test_the_plan_and_ceiling_use_the_chosen_models_prices(model: str, ceiling: str) -> None:
    spy = Spy()
    code, out, _ = run(spy, max_usd=None, model=model, effort="high")
    assert code == 2
    assert f"model {model}, effort high" in out
    # 18 x ($1.50 + 24 000 output tokens at $10 (Sonnet) or at Haiku's upper tier, $2.50).
    assert f"estimated cost ceiling for this run: {ceiling}" in out
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_repeat_multiplies_the_runs_and_the_ceiling() -> None:
    spy = Spy()
    code, out, _ = run(spy, max_usd=None, model="claude-sonnet-5-5", repeat=3)
    assert code == 2
    assert "18 cases x 3 repeats = 54 runs" in out
    assert "estimated cost ceiling for this run: $93.96" in out
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_the_effort_defaults_to_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_AGENT_EFFORT", "low")
    code, out, _ = run(Spy(), max_usd=None, model="claude-haiku-5-5")
    assert code == 2
    assert "effort low" in out


@pytest.mark.usefixtures("key")
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"model": "claude-unpriced-1"}, "no price"),
        ({"model": "claude-haiku-5-5-20990101"}, "no price"),
        ({"model": "not a model!"}, "--model"),
        ({"effort": "extreme"}, "--effort"),
        ({"repeat": 0}, "--repeat"),
        ({"repeat": 11}, "--repeat"),
    ],
)
def test_it_refuses_a_bad_option_before_installing_or_building_anything(
    kwargs: dict[str, Any], message: str
) -> None:
    spy = Spy()
    code, out, err = run(spy, **kwargs)
    assert code == 2
    assert message in err
    assert out == ""
    assert nothing_ran(spy)


@pytest.mark.usefixtures("key")
def test_the_other_refusals_stay_with_the_new_options(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy()
    assert run(spy, live=False, model="claude-sonnet-5-5")[0] == 2
    assert "--max-usd" in run(spy, max_usd=None, model="claude-sonnet-5-5")[2]
    monkeypatch.setenv("CI", "1")
    assert run(spy, model="claude-sonnet-5-5")[0] == 2
    assert nothing_ran(spy)


def _submit_replies(case_name: str, model: str, usage: tuple[int, int, int, int]) -> list[Reply]:
    case = CASES[case_name]
    return [
        stream(message(tool_use("t1", "get_music", {}), stop="tool_use", model=model)),
        stream(
            message(
                tool_use("t2", "submit_draft", {"draft": good_draft(case)}),
                stop="tool_use",
                model=model,
                usage=usage,
            )
        ),
    ]


@pytest.mark.usefixtures("key")
async def test_a_sonnet_run_sends_its_model_and_effort_prices_it_and_records_the_metrics(
    tmp_path: Path,
) -> None:
    replies = [
        *_submit_replies("minimum_10", "claude-sonnet-5-5", (5000, 2000, 0, 0)),
        *_submit_replies("minimum_10", "claude-sonnet-5-5", (5000, 2000, 0, 0)),
    ]
    spy = Spy(FakeAnthropic(replies))
    results = tmp_path / "results.json"
    code, out, err = await _in_thread(
        run,
        spy,
        cases=["minimum_10"],
        out_path=results,
        model="claude-sonnet-5-5",
        effort="high",
        repeat=2,
    )
    assert code == 0, (out, err)
    assert spy.fake is not None
    assert {b["model"] for b in spy.fake.bodies} == {"claude-sonnet-5-5"}
    assert {json.dumps(b["output_config"]) for b in spy.fake.bodies} == {'{"effort": "high"}'}
    assert "minimum_10 #1: pass (submitted)" in out
    assert "minimum_10 #2: pass" in out
    assert "2 of 2 passed" in out
    data = json.loads(results.read_text("utf-8"))["results"]
    assert [r["repeat"] for r in data] == [0, 1]
    first = data[0]
    assert (first["model"], first["effort"]) == ("claude-sonnet-5-5", "high")
    assert first["model_answered"] == "claude-sonnet-5-5"
    # (1200 + 5000) input at $2 and (300 + 2000) output at $10 per million, over two turns.
    assert first["cost_micros"] == (1200 + 5000) * 2 + (300 + 2000) * 10
    assert (first["input_tokens"], first["output_tokens"]) == (6200, 2300)
    assert (first["turns"], first["tool_calls"], first["corrections"]) == (2, 2, 0)
    assert first["wall_sec"] >= 0
    assert first["prompt_version"]
    assert first["tss_bounds"]
    assert first["tss"] > 0
    assert first["changes_on_music"]
    assert [c["name"] for c in first["checks"]][:2] == ["structure", "validate"]
    assert first["timeline"]
    assert PLANTED_KEY not in results.read_text("utf-8")


@pytest.mark.usefixtures("key")
async def test_a_haiku_turn_over_100000_prompt_tokens_is_billed_at_the_upper_tier(
    tmp_path: Path,
) -> None:
    spy = Spy(FakeAnthropic(_submit_replies("minimum_10", "claude-haiku-5-5", (5000, 2000, 0, 0))))
    results = tmp_path / "r.json"
    # The first turn (1200 in, 300 out) is in the lower tier, the second (5000 in) too.
    code, _, _ = await _in_thread(
        run, spy, cases=["minimum_10"], out_path=results, model="claude-haiku-5-5"
    )
    assert code == 0
    row = json.loads(results.read_text("utf-8"))["results"][0]
    assert row["cost_micros"] == 6200 * 0.1 + 2300 * 0.5
    big = [
        stream(
            message(
                tool_use("t1", "get_music", {}),
                stop="tool_use",
                model="claude-haiku-5-5",
                usage=(150_000, 100, 0, 0),
            )
        ),
        *_submit_replies("minimum_10", "claude-haiku-5-5", (5000, 2000, 0, 0))[1:],
    ]
    spy = Spy(FakeAnthropic(big))
    code, _, _ = await _in_thread(
        run, spy, cases=["minimum_10"], out_path=results, model="claude-haiku-5-5"
    )
    row = json.loads(results.read_text("utf-8"))["results"][0]
    assert row["cost_micros"] == 150_000 * 0.5 + 100 * 2.5 + 5000 * 0.1 + 2000 * 0.5


async def test_each_failed_check_is_printed_with_its_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(case: Any, provider: Any, config: Any, **kwargs: Any) -> CaseResult:
        checks = [
            Check("duration", True, "1800 s for 1800 s asked"),
            Check("tss", False, "TSS 38.2, bounds 40 to 75"),
        ]
        return CaseResult(case.name, "submitted", checks, repeat=kwargs["repeat"])

    monkeypatch.setattr(live, "run_case", failing)
    out = io.StringIO()
    await live.run_live([CASES["long_60"]], lambda: _script(1), AgentConfig(), 5_000_000, out)
    text = out.getvalue()
    assert "long_60: FAIL (submitted)" in text
    assert "  long_60 failed tss: TSS 38.2, bounds 40 to 75" in text
    assert "duration" not in text  # a passed check prints nothing


async def test_repeats_run_all_cases_once_before_any_case_again() -> None:
    out = io.StringIO()
    cases = [CASES["easy_30"], CASES["minimum_10"]]
    results = await live.run_live(
        cases, lambda: _script(10), AgentConfig(model="fake-model"), 5_000_000, out, repeat=2
    )
    assert [(r.name, r.repeat) for r in results] == [
        ("easy_30", 0),
        ("minimum_10", 0),
        ("easy_30", 1),
        ("minimum_10", 1),
    ]
    assert {r.model for r in results} == {"fake-model"}


def test_the_default_installer_puts_the_runs_model_host_on_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #238 review: the live run installs the worker list with its provider's host, through
    the same per-provider builder as the worker, never a list without a model host."""
    import inspect
    import urllib.request

    from classbeat import egress

    assert (
        inspect.signature(main_live).parameters["install"].default is live.install_model_allowlist
    )
    monkeypatch.setattr(urllib.request, "_opener", None)
    built: list[tuple[str, ...]] = []
    real = egress.worker_allowlist

    def recording(model_providers: Any = ()) -> egress.Allowlist:
        built.append(tuple(model_providers))
        return real(model_providers)

    monkeypatch.setattr(egress, "worker_allowlist", recording)
    opener = live.install_model_allowlist()
    assert built == [("anthropic",)]
    assert "api.anthropic.com" in real(built[0]).hosts
    assert isinstance(opener, urllib.request.OpenerDirector)
    for host in ("api.openai.com", "api.deepseek.com", "evil.example"):
        with pytest.raises(egress.OutboundBlocked):
            opener.open(f"https://{host}/", timeout=1)
