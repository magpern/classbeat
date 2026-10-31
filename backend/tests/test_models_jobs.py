"""Model jobs resolve their provider from routes (ADR 0012 section 5, "Resolving"; M3c G2, #215).

``design_draft`` runs through the worker's runner with the fake MOWL and the worker's real
``RouteModels``: a route's choice builds a **replay** provider (the real ``OpenAIProvider`` on a
scripted transport), no route is the bootstrap, a cleared route is the bootstrap for the next
job, an unusable route fails ``model_config`` before any request, a stale one is used and logged
once, and ``fake`` bypasses routes. The route key comes from the job input, fixed at creation.

OpenAI ships ``owner_pending`` (ADR 0012 §6), so its route is reachable only under a simulated
owner "yes" (``owner_accepts``): ``gpt-6.1-sol`` (catalogued and priced) as ``allowed`` here.
Fakes only: no key is real, nothing leaves the process (netguard).
"""

import json
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from agentfake import CONFIG, SCRIPTS, provider
from classbeat import design
from classbeat.agent import catalog
from classbeat.agent.catalog import CatalogModel
from classbeat.agent.provider import ModelProvider
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.design import Runtime
from classbeat.egress import MODEL_HOSTS
from classbeat.models import ModelChoice, clear_route, set_route
from classbeat.models import routes as routes_module
from classbeat.models.providers import RouteModels
from designtools import DesignEnv, FakeMowlMusic
from jobtools import new_job
from modeltools import OPUS, add_result, clear_gate, owner_accepts, settings
from openaifake import OPENAI_MODEL, ScriptedResponses, scripted_openai

pytestmark = pytest.mark.usefixtures("jenv")

SOL = OPENAI_MODEL  # "gpt-6.1-sol": in the catalog and the price table
CANARY = "sk-canary-not-a-real-key-0123456789abcdef"


@dataclass
class Builds:
    """``RouteModels.build``: records every choice it is given and answers a replay
    ``OpenAIProvider`` for an OpenAI choice, the fake provider for the bootstrap."""

    script: str = "good_first_draft"
    chosen: list[ModelChoice] = field(default_factory=lambda: list[ModelChoice]())
    replays: list[ScriptedResponses] = field(default_factory=lambda: list[ScriptedResponses]())

    def __call__(self, _settings: Settings, choice: ModelChoice) -> ModelProvider:
        self.chosen.append(choice)
        if choice.provider == "openai":
            made, api = scripted_openai(provider(self.script))
            self.replays.append(api)
            return made
        return provider(self.script)


@pytest.fixture
def accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's "yes" to OpenAI and DeepSeek, simulated for one test: ``gpt-6.1-sol`` and a
    test DeepSeek entry are ``allowed``."""
    owner_accepts(monkeypatch, "openai", "deepseek")
    sol = catalog.find("openai", SOL)
    assert sol is not None
    assert sol.data_policy == "owner_pending"
    others = tuple(e for e in catalog.CATALOG if e is not sol)
    deepseek = CatalogModel("deepseek", "deepseek-ok", ("medium",), MODEL_HOSTS["deepseek"],
                            "allowed")  # fmt: skip
    monkeypatch.setattr(
        catalog, "CATALOG", (*others, replace(sol, data_policy="allowed"), deepseek)
    )


@pytest.fixture
def gate(denv: DesignEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    clear_gate(denv.admin)
    monkeypatch.setattr(routes_module, "_warned", set[Any]())  # a fresh "process"
    yield
    clear_gate(denv.admin)


def worker(app_url: str, kek_dir: Path, *providers: str, **overrides: Any) -> Settings:
    for name in providers:
        (kek_dir / f"{name}_api_key").write_text(CANARY + "\n", "utf-8")
    return settings(app_url, **overrides)


def use(denv: DesignEnv, s: Settings, builds: Builds | None = None) -> Builds:
    made = builds or Builds()
    design.configure(
        Runtime(keyring=denv.ops.keyring, models=RouteModels(s, build=made), agent=CONFIG)
    )
    return made


async def enable(denv: DesignEnv, s: Settings, model: str = SOL, **kw: Any) -> uuid.UUID:
    provider_name = kw.pop("provider", "openai")
    key = kw.pop("key", "default")
    result_id = add_result(denv.admin, provider_name, model, "medium", "design", **kw)
    async with system_session(denv.ops.factory) as db:
        await set_route(db, key, "design", provider_name, model, "medium", "cli", settings=s)
    return result_id


async def owner_job(denv: DesignEnv, route_key: str | None = None) -> uuid.UUID:
    owner = await denv.ops.user()
    conn = await denv.ops.connect(owner)
    body = denv.input(conn)
    if route_key is not None:
        body["route_key"] = route_key  # as the server stores it (M5 fills it at creation)
    return new_job(denv.admin, owner, kind=design.KIND, input=json.dumps(body))


def runs(denv: DesignEnv, job_id: uuid.UUID) -> list[Any]:
    return denv.agent_runs(job_id)


def stored(denv: DesignEnv, job_id: uuid.UUID) -> dict[str, Any]:
    [revision] = denv.revisions(job_id)
    return dict(revision.provenance)


async def test_a_job_with_a_route_runs_on_the_routes_replay_provider_and_records_it(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None,
) -> None:  # fmt: skip
    s = worker(app_database_url, kek_dir, "anthropic", "openai")
    result_id = await enable(denv, s)
    builds = use(denv, s)
    job_id = await owner_job(denv)
    await denv.run_next()

    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("completed", None)
    assert builds.chosen == [ModelChoice("openai", SOL, "medium", "default", result_id)]
    [api] = builds.replays
    assert api.problems == []
    assert len(api.requests) >= 2
    [run] = runs(denv, job_id)
    assert (run.provider, run.model, run.effort) == ("openai", SOL, "medium")
    assert (run.route_key, run.eval_result_id) == ("default", result_id)
    made = stored(denv, job_id)
    assert (made["provider"], made["model"]) == ("openai", SOL)
    assert (made["route_key"], made["eval_result_id"]) == ("default", str(result_id))


async def test_without_a_route_the_bootstrap_runs_and_records_no_route(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str, gate: None
) -> None:
    s = worker(app_database_url, kek_dir, "anthropic")
    builds = use(denv, s)
    job_id = await owner_job(denv)
    await denv.run_next()

    assert denv.ops.row(job_id).state == "completed"
    assert builds.chosen == [ModelChoice("anthropic", s.agent_model, s.agent_effort)]
    [run] = runs(denv, job_id)
    assert (run.model, run.route_key, run.eval_result_id) == (OPUS, None, None)
    made = stored(denv, job_id)
    assert (made["route_key"], made["eval_result_id"]) == (None, None)


async def test_a_route_cleared_between_two_jobs_applies_to_the_next_one(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None,
) -> None:  # fmt: skip
    s = worker(app_database_url, kek_dir, "anthropic", "openai")
    await enable(denv, s)
    builds = use(denv, s)
    first = await owner_job(denv)
    await denv.run_next()
    async with system_session(denv.ops.factory) as db:
        await clear_route(db, "default", "design", "cli")
    second = await owner_job(denv)
    await denv.run_next()

    assert [denv.ops.row(j).state for j in (first, second)] == ["completed", "completed"]
    assert [c.provider for c in builds.chosen] == ["openai", "anthropic"]
    assert [runs(denv, j)[0].route_key for j in (first, second)] == ["default", None]


async def test_the_route_key_comes_from_the_job_input(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None,
) -> None:  # fmt: skip
    s = worker(app_database_url, kek_dir, "anthropic", "openai")
    pro = await enable(denv, s, key="pro")
    builds = use(denv, s)
    plain, keyed = await owner_job(denv), await owner_job(denv, "pro")
    await denv.run_next()
    await denv.run_next()

    by_job = {j: runs(denv, j)[0] for j in (plain, keyed)}
    assert (by_job[plain].route_key, by_job[plain].provider) == (None, "fake")  # bootstrap
    assert (by_job[keyed].route_key, by_job[keyed].eval_result_id) == ("pro", pro)
    assert sorted(c.provider for c in builds.chosen) == ["anthropic", "openai"]


def _gone(monkeypatch: pytest.MonkeyPatch, kek_dir: Path) -> dict[str, Any]:
    monkeypatch.setattr(catalog, "CATALOG", tuple(e for e in catalog.CATALOG if e.model != SOL))
    return {}


def _no_key(monkeypatch: pytest.MonkeyPatch, kek_dir: Path) -> dict[str, Any]:
    (kek_dir / "openai_api_key").unlink()
    return {}


def _reverted(monkeypatch: pytest.MonkeyPatch, kek_dir: Path) -> dict[str, Any]:
    entries = [
        replace(e, data_policy="owner_pending") if e.model == SOL else e for e in catalog.CATALOG
    ]
    monkeypatch.setattr(catalog, "CATALOG", tuple(entries))
    return {}


@pytest.mark.parametrize(
    ("change", "reason"),
    [(_gone, "not_in_catalog"), (_no_key, "no_key"), (_reverted, "data_policy")],
)
async def test_an_unusable_route_fails_model_config_before_any_request(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, change: Any, reason: str,
) -> None:  # fmt: skip
    s = worker(app_database_url, kek_dir, "anthropic", "openai")
    await enable(denv, s)
    change(monkeypatch, kek_dir)
    s = settings(app_database_url)  # the worker after the change (key files read at start)
    builds = use(denv, s)
    job_id = await owner_job(denv)
    with caplog.at_level(logging.WARNING, logger="classbeat.models.routes"):
        await denv.run_next()

    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert builds.chosen == []
    assert builds.replays == []
    assert fake_mowl.requests == []  # before any MOWL call
    assert runs(denv, job_id) == []
    [line] = [r for r in caplog.records if r.getMessage() == "model route unusable"]
    assert (line.route_key, line.stage_group, line.reason) == ("default", "design", reason)  # pyright: ignore[reportAttributeAccessIssue]


async def test_a_deepseek_route_with_the_setting_switched_off_fails_model_config(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None,
) -> None:  # fmt: skip
    on = worker(app_database_url, kek_dir, "anthropic", "deepseek", deepseek_enabled=True)
    await enable(denv, on, "deepseek-ok", provider="deepseek")
    builds = use(denv, settings(app_database_url, deepseek_enabled=False))
    job_id = await owner_job(denv)
    await denv.run_next()

    assert (denv.ops.row(job_id).state, denv.ops.row(job_id).error_category) == (
        "failed", "model_config",
    )  # fmt: skip
    assert builds.chosen == []
    assert fake_mowl.requests == []


@pytest.mark.parametrize("reason", ["manifest_digest", "suite_version", "prompt_versions"])
async def test_a_route_stale_for_a_suite_or_code_change_is_used_and_logged_once_per_process(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None, caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch, reason: str,
) -> None:  # fmt: skip
    s = worker(app_database_url, kek_dir, "anthropic", "openai")
    result_id = await enable(denv, s, age_minutes=10)
    changed = {
        "manifest_digest": ("running_manifest_digest", lambda: "c" * 64),
        "suite_version": ("running_suite_version", lambda: 99),
        "prompt_versions": ("running_prompt_versions", lambda group: {"design": "d" * 64}),
    }
    name, value = changed[reason]
    monkeypatch.setattr(routes_module, name, value)  # a deploy after the route was set
    builds = use(denv, s)
    jobs = [await owner_job(denv), await owner_job(denv)]
    with caplog.at_level(logging.WARNING, logger="classbeat.models.routes"):
        await denv.run_next()
        await denv.run_next()

    assert [denv.ops.row(j).state for j in jobs] == ["completed", "completed"]
    assert [c.stale for c in builds.chosen] == [(reason,), (reason,)]
    assert [runs(denv, j)[0].eval_result_id for j in jobs] == [result_id, result_id]
    lines = [r for r in caplog.records if r.getMessage() == "model route stale"]
    assert [(r.route_key, r.stage_group, r.reason) for r in lines] == [  # pyright: ignore[reportAttributeAccessIssue]
        ("default", "design", reason)
    ]
    assert set(vars(lines[0])) >= {"route_key", "stage_group", "reason"}
    assert CANARY not in caplog.text


async def test_a_route_with_a_newer_failing_result_fails_model_config_before_any_request(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None, caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    """A newer failure means no standing pass (ADR 0012 amendment, G2): refused, not used,
    even when other reasons also apply; no fallback to the bootstrap."""
    s = worker(app_database_url, kek_dir, "anthropic", "openai")
    await enable(denv, s, age_minutes=10)
    add_result(denv.admin, "openai", SOL, "medium", "design", passed=False)  # newer, failing
    builds = use(denv, s)
    job_id = await owner_job(denv)
    with caplog.at_level(logging.WARNING, logger="classbeat.models.routes"):
        await denv.run_next()

    row = denv.ops.row(job_id)
    assert (row.state, row.error_category) == ("failed", "model_config")
    assert builds.chosen == []
    assert builds.replays == []
    assert fake_mowl.requests == []
    assert runs(denv, job_id) == []
    [line] = [r for r in caplog.records if r.getMessage() == "model route unusable"]
    assert (line.route_key, line.stage_group, line.reason) == (  # pyright: ignore[reportAttributeAccessIssue]
        "default", "design", "newer_failure",
    )  # fmt: skip
    assert not [r for r in caplog.records if r.getMessage() == "model route stale"]


async def test_fake_bypasses_routes(
    denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path, app_database_url: str,
    accepted: None, gate: None, tmp_path: Path,
) -> None:  # fmt: skip
    s = worker(app_database_url, kek_dir, "anthropic", "openai")
    await enable(denv, s)
    script = SCRIPTS / "good_first_draft.json"
    fake = settings(app_database_url, model_provider="fake", fake_model_script=script)
    builds = use(denv, fake)
    job_id = await owner_job(denv)
    await denv.run_next()

    assert denv.ops.row(job_id).state == "completed"
    assert builds.chosen == []  # no route read, no choice built
    [run] = runs(denv, job_id)
    assert (run.provider, run.model, run.route_key, run.eval_result_id) == (
        "fake", CONFIG.model, None, None,
    )  # fmt: skip
