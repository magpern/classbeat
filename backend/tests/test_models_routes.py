"""``classbeat.models`` (ADR 0012 section 5; #211): ``set_route`` and its refusals,
``resolve_route`` and its re-checks, staleness, ``record_result``, ``provider_for_choice``, the
``models`` CLI and what the web process loads. Fakes only: no key is real, no provider is called."""

import json
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classbeat.agent import catalog
from classbeat.agent.catalog import CatalogModel
from classbeat.agent.providers import AnthropicProvider, OpenAIProvider, ProviderUnavailable
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.evals.gate import CategoryVerdict, GroupVerdict, Status
from classbeat.jobs.errors import JobFailure
from classbeat.models import (
    DEFAULT_ROUTE_KEY,
    ModelChoice,
    RouteRefused,
    RouteUnusable,
    clear_route,
    list_results,
    list_routes,
    resolve_route,
    set_route,
)
from classbeat.models import routes as routes_module
from classbeat.models.providers import provider_for_choice
from classbeat.models.store import ResultInvalid, record_result
from classbeat.models.versions import (
    GROUP_STAGES,
    all_prompt_versions,
    running_manifest_digest,
    running_prompt_versions,
    running_suite_version,
)
from jobtools import engine_for
from modeltools import (
    DEEPSEEK_PENDING,
    OPUS,
    PENDING_OPENAI,
    ZEROS,
    AcceptedEntries,
    add_result,
    clear_gate,
    extend_catalog,
    models_cli,
    settings,
)

Factory = async_sessionmaker[AsyncSession]
CANARY = "sk-canary-not-a-real-key-0123456789abcdef"


@pytest.fixture
def admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        clear_gate(connection)
        yield connection
        clear_gate(connection)


@pytest.fixture
async def factory(app_database_url: str) -> AsyncIterator[Factory]:
    engine = create_async_engine(app_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def web(app_database_url: str) -> Settings:
    """The web process's settings: no model key file (``kek_dir`` holds only the KEK)."""
    return settings(app_database_url)


@pytest.fixture
def catalog_plus(monkeypatch: pytest.MonkeyPatch) -> AcceptedEntries:
    return extend_catalog(monkeypatch)


def secret_files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def worker(app_url: str, kek_dir: Path, *providers: str, **overrides: Any) -> Settings:
    """The worker's settings, with a (fake) key file for each of ``providers``."""
    for provider in providers:
        (kek_dir / f"{provider}_api_key").write_text(CANARY + "\n", "utf-8")
    return settings(app_url, **overrides)


async def enable(
    factory: Factory,
    s: Settings,
    provider: str,
    model: str,
    effort: str,
    group: str = "design",
    *,
    key: str = "default",
) -> None:
    async with system_session(factory) as db:
        await set_route(db, key, group, provider, model, effort, "admin@example.com", settings=s)


async def refused(
    factory: Factory,
    s: Settings,
    provider: str,
    model: str,
    effort: str,
    group: str = "design",
    *,
    key: str = "default",
) -> str:
    with pytest.raises(RouteRefused) as caught:
        await enable(factory, s, provider, model, effort, group, key=key)
    return caught.value.reason


async def resolve(
    factory: Factory, s: Settings, group: str = "design", key: str = "default"
) -> Any:
    async with system_session(factory) as db:
        return await resolve_route(db, key, group, settings=s)


async def unusable(factory: Factory, s: Settings, group: str = "design") -> str:
    with pytest.raises(RouteUnusable) as caught:
        await resolve(factory, s, group)
    assert isinstance(caught.value, JobFailure)
    assert caught.value.category == "model_config"
    return caught.value.reason


# --- the running versions ---------------------------------------------------------------------


def test_the_running_prompt_versions_are_those_of_stages_built_from_real_contexts() -> None:
    from classbeat.agent.design import DesignContext, design_stage
    from classbeat.agent.revise import ReviseContext, revise_stage
    from classbeat.evals.cases import load_cases
    from classbeat.evals.music import build_music

    cases = load_cases()
    design_case = next(c for c in cases if c.revise is None)
    revise_case = next(c for c in cases if c.revise is not None)
    assert revise_case.revise is not None
    music = build_music(design_case.music)
    design = design_stage(DesignContext.build(design_case.brief, music))
    rmusic = build_music(revise_case.music)
    revise = revise_stage(
        ReviseContext.build(
            revise_case.brief, rmusic, revise_case.revise.base, (), "easier", music_changed=False
        )
    )
    assert running_prompt_versions("design") == {
        "design": design.prompt_version,
        "revise": revise.prompt_version,
    }


async def test_the_song_stages_running_versions_are_what_a_real_run_records() -> None:
    from agentfake import MemoryHost
    from pickfake import good_pick_provider, run_both, world

    pooled, chosen = await run_both(world(), good_pick_provider(), MemoryHost())
    assert running_prompt_versions("songs") == {
        "pick_pool": pooled.info.prompt_version,
        "choose_songs": chosen.info.prompt_version,
    }
    assert set(all_prompt_versions()) == {s for g in GROUP_STAGES.values() for s in g}


def test_the_suite_version_is_the_manifests_without_loading_the_eval_package() -> None:
    from classbeat.evals.suite import load_suite

    assert running_suite_version() == load_suite().manifest.suite_version
    # The stored digest stands for the running suite: load_suite refuses one that is not.
    assert running_manifest_digest() == load_suite().manifest.digest


def test_the_web_side_of_the_gate_loads_no_provider_no_sdk_and_no_eval_code() -> None:
    probe = (
        "import json, sys\n"
        "import classbeat.models, classbeat.models.cli, classbeat.models.routes, classbeat.cli\n"
        "from classbeat.models.versions import running_suite_version\n"
        "running_suite_version()\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run(  # noqa: S603 - our own interpreter, a fixed probe
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, check=True
    )
    loaded = json.loads(result.stdout.splitlines()[-1])
    forbidden = ("classbeat.agent.providers", "classbeat.models.providers", "classbeat.evals")
    assert [m for m in loaded if m.startswith(forbidden)] == []
    assert [m for m in loaded if m.split(".")[0] in ("anthropic", "openai")] == []


# --- set_route --------------------------------------------------------------------------------


async def test_a_route_is_set_at_the_newest_pass_without_any_key_file(
    admin: Connection, factory: Factory, web: Settings, kek_dir: Path, app_database_url: str
) -> None:
    """The web process's view: no key file, and set_route never asks for one; the worker then
    refuses the route at resolution (``model_config``) until the key is there."""
    assert secret_files(kek_dir) == ["connection_kek"]
    add_result(admin, age_minutes=10)
    newest = add_result(admin, age_minutes=1)
    await enable(factory, web, "anthropic", OPUS, "medium")
    (view,) = await _routes(factory)
    assert (view.route_key, view.stage_group, view.provider, view.model, view.effort) == (
        "default", "design", "anthropic", OPUS, "medium",
    )  # fmt: skip
    assert view.result.id == newest
    assert view.set_by == "admin@example.com"
    assert view.stale == ()
    assert await unusable(factory, web) == "no_key"
    choice = await resolve(factory, worker(app_database_url, kek_dir, "anthropic"))
    assert choice == ModelChoice("anthropic", OPUS, "medium", "default", newest, ())


async def _routes(factory: Factory) -> Any:
    async with system_session(factory) as db:
        return await list_routes(db)


async def test_set_route_refuses_a_model_or_effort_outside_the_catalog(
    admin: Connection, factory: Factory, web: Settings, catalog_plus: AcceptedEntries
) -> None:
    add_result(admin)
    assert await refused(factory, web, "anthropic", "claude-opus-9", "medium") == "not_in_catalog"
    assert await refused(factory, web, "fake", "fake", "medium") == "not_in_catalog"
    assert await refused(factory, web, "anthropic", "Claude-Opus-5-5", "medium") == (
        "not_in_catalog"
    )
    assert await refused(factory, web, "openai", "gpt-allowed", "high") == "effort_not_in_catalog"
    assert await refused(factory, web, "anthropic", OPUS, "ultra") == "effort_not_in_catalog"


async def test_set_route_refuses_owner_pending_and_disabled_deepseek(
    admin: Connection,
    factory: Factory,
    web: Settings,
    catalog_plus: AcceptedEntries,
    app_database_url: str,
) -> None:
    for entry in (PENDING_OPENAI, DEEPSEEK_PENDING, catalog_plus.deepseek):
        add_result(admin, entry.provider, entry.model, "medium")
    assert await refused(factory, web, "openai", "gpt-test", "medium") == "data_policy"
    assert await refused(factory, web, "deepseek", "deepseek-ok", "medium") == "deepseek_disabled"
    assert await refused(factory, web, "deepseek", "deepseek-test", "medium") == (
        "deepseek_disabled"
    )
    on = settings(app_database_url, deepseek_enabled=True)
    assert await refused(factory, on, "deepseek", "deepseek-test", "medium") == "data_policy"
    await enable(factory, on, "deepseek", "deepseek-ok", "medium")  # allowed and on: it is set


def test_the_catalog_type_refuses_allowed_for_openai_and_deepseek() -> None:
    """The type's own check, unpatched: only an accepted provider's entry may be ``allowed``."""
    assert frozenset({"anthropic"}) == catalog.USER_DATA_PROVIDERS
    for provider in ("openai", "deepseek"):
        host = catalog.MODEL_HOSTS[provider]
        with pytest.raises(ValueError, match="owner accepted"):
            CatalogModel(provider, "m-1", ("medium",), host, "allowed")  # type: ignore[arg-type]
        CatalogModel(provider, "m-1", ("medium",), host, "owner_pending")  # type: ignore[arg-type]


def test_openai_and_deepseek_ship_owner_pending() -> None:
    for entry in catalog.CATALOG:
        assert entry.data_policy == (
            "allowed" if entry.provider == "anthropic" else "owner_pending"
        )


async def test_set_route_needs_a_current_passing_newest_result(
    admin: Connection, factory: Factory, web: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await refused(factory, web, "anthropic", OPUS, "medium") == "no_result"
    # Another effort, model or group does not count.
    add_result(admin, effort="high")
    add_result(admin, model="claude-sonnet-5-5")
    add_result(admin, group="songs")
    assert await refused(factory, web, "anthropic", OPUS, "medium") == "no_result"
    # Only a result of an older suite version: a pass now, then the code moves on.
    add_result(admin, age_minutes=30)
    with monkeypatch.context() as m:
        m.setattr(routes_module, "running_suite_version", lambda: running_suite_version() + 1)
        assert await refused(factory, web, "anthropic", OPUS, "medium") == "suite_version"
    # A pass, then a newer failure: the newest decides.
    add_result(admin, age_minutes=20)
    add_result(admin, passed=False, age_minutes=10)
    assert await refused(factory, web, "anthropic", OPUS, "medium") == "result_failed"
    # A newer pass with other prompt versions.
    other = {**running_prompt_versions("design"), "revise": "f" * 64}
    add_result(admin, prompt_versions=other, age_minutes=5)
    assert await refused(factory, web, "anthropic", OPUS, "medium") == "prompt_versions"
    missing = {"design": running_prompt_versions("design")["design"]}
    add_result(admin, prompt_versions=missing, age_minutes=4)
    assert await refused(factory, web, "anthropic", OPUS, "medium") == "prompt_versions"
    # A newer pass recorded on another suite of the same version (#215: the manifest digest).
    add_result(admin, manifest_digest="c" * 64, age_minutes=3)
    assert await refused(factory, web, "anthropic", OPUS, "medium") == "manifest_digest"
    # The songs group's prompts do not matter to a design result.
    extra = {**running_prompt_versions("design"), "pick_pool": "e" * 64}
    good = add_result(admin, prompt_versions=extra, age_minutes=1)
    await enable(factory, web, "anthropic", OPUS, "medium")
    assert (await _routes(factory))[0].result.id == good


async def test_set_route_refuses_bad_input(
    admin: Connection, factory: Factory, web: Settings
) -> None:
    add_result(admin)
    assert await refused(factory, web, "anthropic", OPUS, "medium", key="Default") == (
        "invalid_route_key"
    )
    assert await refused(factory, web, "anthropic", OPUS, "medium", key="") == "invalid_route_key"
    assert await refused(factory, web, "anthropic", OPUS, "medium", "all") == "invalid_stage_group"
    async with system_session(factory) as db:
        for actor in ("", "x" * 201):
            with pytest.raises(RouteRefused, match="invalid_actor"):
                await set_route(db, "default", "design", "anthropic", OPUS, "medium", actor,
                                settings=web)  # fmt: skip


async def test_set_route_works_inside_the_callers_transaction(
    admin: Connection, factory: Factory, web: Settings
) -> None:
    add_result(admin)

    class Abort(Exception):
        pass

    async def set_then_abort() -> None:
        async with system_session(factory) as db:
            await set_route(db, "pro", "design", "anthropic", OPUS, "medium", "a", settings=web)
            # Visible in the caller's transaction, not committed by set_route.
            assert len(await list_routes(db)) == 1
            assert admin.execute(text("SELECT count(*) FROM model_routes")).scalar_one() == 0
            raise Abort

    with pytest.raises(Abort):
        await set_then_abort()
    assert admin.execute(text("SELECT count(*) FROM model_routes")).scalar_one() == 0
    # A refusal leaves the caller's transaction usable (its audit row can still be written).
    async with system_session(factory) as db:
        with pytest.raises(RouteRefused):
            await set_route(db, "pro", "songs", "anthropic", OPUS, "medium", "a", settings=web)
        await set_route(db, "pro", "design", "anthropic", OPUS, "medium", "a", settings=web)
    assert admin.execute(text("SELECT count(*) FROM model_routes")).scalar_one() == 1


async def test_setting_a_route_again_moves_it_and_clearing_it_removes_it(
    admin: Connection, factory: Factory, web: Settings, app_database_url: str, kek_dir: Path
) -> None:
    add_result(admin, age_minutes=5)
    await enable(factory, web, "anthropic", OPUS, "medium")
    add_result(admin, effort="high", age_minutes=1)
    await enable(factory, web, "anthropic", OPUS, "high")
    (view,) = await _routes(factory)
    assert view.effort == "high"
    async with system_session(factory) as db:
        await clear_route(db, "default", "design", "admin@example.com")
        with pytest.raises(RouteRefused, match="no_route"):
            await clear_route(db, "default", "design", "admin@example.com")
    s = worker(app_database_url, kek_dir, "anthropic", agent_model="claude-haiku-5-5")
    assert await resolve(factory, s) == ModelChoice("anthropic", "claude-haiku-5-5", "medium")


# --- resolve_route ----------------------------------------------------------------------------


async def test_no_route_gives_the_bootstrap_choice(
    admin: Connection, factory: Factory, app_database_url: str
) -> None:
    s = settings(app_database_url, agent_model="claude-sonnet-5-5", agent_effort="high")
    for group in ("design", "songs"):
        choice = await resolve(factory, s, group, DEFAULT_ROUTE_KEY)
        assert choice == ModelChoice("anthropic", "claude-sonnet-5-5", "high", None, None, ())
    # A route for another key or group is not this one's.
    add_result(admin)
    await enable(factory, s, "anthropic", OPUS, "medium", key="pro")
    assert (await resolve(factory, s, "design")).route_key is None
    assert (await resolve(factory, s, "songs", "pro")).route_key is None
    with pytest.raises(JobFailure):
        await resolve(factory, s, "design", "Not A Key")


async def test_a_route_to_another_provider_is_used_when_every_check_holds(
    admin: Connection,
    factory: Factory,
    web: Settings,
    catalog_plus: AcceptedEntries,
    app_database_url: str,
    kek_dir: Path,
) -> None:
    result = add_result(admin, "openai", "gpt-allowed", "medium", "songs")
    await enable(factory, web, "openai", "gpt-allowed", "medium", "songs")
    assert await unusable(factory, web, "songs") == "no_key"
    s = worker(app_database_url, kek_dir, "openai")
    assert await resolve(factory, s, "songs") == ModelChoice(
        "openai", "gpt-allowed", "medium", "default", result, ()
    )
    assert (await resolve(factory, s, "design")).provider == "anthropic"  # no design route


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("entry_gone", "not_in_catalog"),
        ("effort_gone", "effort_not_in_catalog"),
        ("policy_reverted", "data_policy"),
        ("key_removed", "no_key"),
    ],
)
async def test_an_unusable_route_fails_model_config_with_no_fallback(
    admin: Connection,
    factory: Factory,
    web: Settings,
    catalog_plus: AcceptedEntries,
    app_database_url: str,
    kek_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    reason: str,
) -> None:
    add_result(admin, "openai", "gpt-allowed", "medium")
    await enable(factory, web, "openai", "gpt-allowed", "medium")
    s = worker(app_database_url, kek_dir, "openai", "anthropic")
    assert (await resolve(factory, s)).provider == "openai"
    allowed = catalog_plus.openai
    others = tuple(e for e in catalog.CATALOG if e is not allowed)
    if change == "entry_gone":
        monkeypatch.setattr(catalog, "CATALOG", others)
    elif change == "effort_gone":
        smaller = CatalogModel("openai", "gpt-allowed", ("low",), allowed.host, "allowed")
        monkeypatch.setattr(catalog, "CATALOG", (*others, smaller))
    elif change == "policy_reverted":
        pending = CatalogModel("openai", "gpt-allowed", ("medium",), allowed.host, "owner_pending")
        monkeypatch.setattr(catalog, "CATALOG", (*others, pending))
    else:
        (kek_dir / "openai_api_key").write_text("", "utf-8")  # empty file = no key
        s = settings(app_database_url)
    assert await unusable(factory, s) == reason


async def test_a_deepseek_route_is_unusable_once_the_setting_is_off(
    admin: Connection,
    factory: Factory,
    catalog_plus: AcceptedEntries,
    app_database_url: str,
    kek_dir: Path,
) -> None:
    on = worker(app_database_url, kek_dir, "deepseek", deepseek_enabled=True)
    add_result(admin, "deepseek", "deepseek-ok", "medium")
    await enable(factory, on, "deepseek", "deepseek-ok", "medium")
    assert (await resolve(factory, on)).provider == "deepseek"
    assert await unusable(factory, settings(app_database_url)) == "deepseek_disabled"


# --- staleness --------------------------------------------------------------------------------


async def test_a_stale_route_is_flagged_with_each_reason_and_a_newer_failure_refused(
    admin: Connection,
    factory: Factory,
    app_database_url: str,
    kek_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = worker(app_database_url, kek_dir, "anthropic")
    result = add_result(admin, age_minutes=10)
    await enable(factory, s, "anthropic", OPUS, "medium")
    assert (await resolve(factory, s)).stale == ()
    # A newer failing result for the same provider, model, effort and group.
    add_result(admin, passed=False, age_minutes=5)
    add_result(admin, passed=False, effort="high", age_minutes=4)  # another effort: not this
    # No standing pass: the worker refuses the route (G2), the listing shows why.
    assert await unusable(factory, s) == "newer_failure"
    [view] = await _routes(factory)
    assert (view.result.id, view.stale) == (result, ("newer_failure",))
    # A deploy changed a prompt or a tool schema.
    changed = {**running_prompt_versions("design"), "design": "d" * 64}
    every = ("prompt_versions", "suite_version", "newer_failure")
    with monkeypatch.context() as m:
        m.setattr(routes_module, "running_prompt_versions", lambda group: changed)
        assert (await _routes(factory))[0].stale == ("prompt_versions", "newer_failure")
        # The suite moved on.
        m.setattr(routes_module, "running_suite_version", lambda: 99)
        assert (await _routes(factory))[0].stale == every
        assert await unusable(factory, s) == "newer_failure"  # with other reasons too
        # A new route for a stale result is refused (not the newest pass of the current code).
        assert await refused(factory, s, "anthropic", OPUS, "medium") == "suite_version"
    assert await refused(factory, s, "anthropic", OPUS, "medium") == "result_failed"
    # Each other reason alone: the route is used and flagged (ADR 0012 section 5).
    clear_gate(admin)
    add_result(admin)
    await enable(factory, s, "anthropic", OPUS, "medium")
    with monkeypatch.context() as m:
        m.setattr(routes_module, "running_prompt_versions", lambda group: changed)
        assert (await resolve(factory, s)).stale == ("prompt_versions",)
    with monkeypatch.context() as m:
        m.setattr(routes_module, "running_suite_version", lambda: 99)
        assert (await resolve(factory, s)).stale == ("suite_version",)
    # The suite's cases changed under the same version (#215): only the manifest digest.
    with monkeypatch.context() as m:
        m.setattr(routes_module, "running_manifest_digest", lambda: "c" * 64)
        assert (await resolve(factory, s)).stale == ("manifest_digest",)
        assert (await _routes(factory))[0].stale == ("manifest_digest",)
        assert await refused(factory, s, "anthropic", OPUS, "medium") == "manifest_digest"


async def test_the_newest_result_is_deterministic_within_one_transaction(
    admin: Connection, factory: Factory, app_database_url: str, kek_dir: Path
) -> None:
    """Two results recorded in one transaction share ``recorded_at`` (``now()``): the later
    insert is the newer one (``seq``, migration 0011), whatever their random ids (#215)."""
    s = worker(app_database_url, kek_dir, "anthropic")
    for _ in range(6):  # uuid4 ids: a random order would show within a few rounds
        clear_gate(admin)
        async with system_session(factory) as db:
            for status in ("passed", "failed"):
                await record_result(db, **_values(verdict=verdict(status)))
        assert await refused(factory, s, "anthropic", OPUS, "medium") == "result_failed"
        async with system_session(factory) as db:
            [newer, older] = await list_results(db)
        assert (newer.passed, older.passed, newer.recorded_at) == (False, True, older.recorded_at)
    # The other order: the pass is the newer one, and the failure before it does not stale it.
    clear_gate(admin)
    async with system_session(factory) as db:
        for status in ("failed", "passed"):
            await record_result(db, **_values(verdict=verdict(status)))
    await enable(factory, s, "anthropic", OPUS, "medium")
    assert (await resolve(factory, s)).stale == ()
    # A failure recorded after the route's pass in one transaction is newer: refused.
    async with system_session(factory) as db:
        await record_result(db, **_values(verdict=verdict("failed")))
    assert await unusable(factory, s) == "newer_failure"
    assert (await _routes(factory))[0].stale == ("newer_failure",)


# --- record_result ----------------------------------------------------------------------------


def verdict(status: Status) -> dict[str, Any]:
    """The gate's own stored form (``GroupVerdict.as_json``)."""
    return GroupVerdict(
        "design", status, 1, ZEROS, 9, 9 if status == "passed" else 4, 0, 100_000, (),
        {"standard": CategoryVerdict(9, 9, 0, ())},
    ).as_json()  # fmt: skip


def _values(**changes: Any) -> dict[str, Any]:
    return {
        "run_id": uuid.uuid4(),
        "suite_version": running_suite_version(),
        "manifest_digest": running_manifest_digest(),
        "provider": "anthropic",
        "model": OPUS,
        "effort": "medium",
        "stage_group": "design",
        "prompt_versions": running_prompt_versions("design"),
        "classbeat_version": "0.3.0",
        "results_sha256": ZEROS,
        "verdict": verdict("passed"),
        "total_cost_micros": 1_234_567,
        "authorization_note": "owner go-ahead in chat, 2026-10-31",
        **changes,
    }


async def record(factory: Factory, **changes: Any) -> Any:
    async with system_session(factory) as db:
        return await record_result(db, **_values(**changes))


async def test_record_result_stores_the_verdict_and_its_passed(
    admin: Connection, factory: Factory, web: Settings
) -> None:
    row = await record(factory)
    assert (row.passed, row.verdict["status"], row.total_cost_micros) == (True, "passed", 1_234_567)
    incomplete = await record(factory, verdict=verdict("incomplete"))
    assert (incomplete.passed, incomplete.verdict["passed"]) == (False, False)
    # The incomplete one is the newest: it blocks a route like a failure.
    assert await refused(factory, web, "anthropic", OPUS, "medium") == "result_failed"
    async with system_session(factory) as db:
        views = await list_results(db)
        assert [v.status for v in views] == ["incomplete", "passed"]
        assert all(v.current for v in views)
        assert await list_results(db, provider="openai") == []
        assert len(await list_results(db, "anthropic", OPUS)) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "fake", "model": "fake"},
        {"model": "claude-opus-9"},
        {"effort": "ultra"},
        {"stage_group": "all"},
        {"verdict": {"status": "passed"}},
        {"verdict": {"passed": "true"}},
        {"verdict": {"passed": 1}},
        {"verdict": {"passed": None}},
        {"prompt_versions": {"design": 1}},
    ],
)
async def test_record_result_refuses_what_could_never_gate(
    admin: Connection, factory: Factory, changes: dict[str, Any]
) -> None:
    with pytest.raises(ResultInvalid):
        await record(factory, **changes)
    assert admin.execute(text("SELECT count(*) FROM model_eval_results")).scalar_one() == 0


async def test_the_database_refuses_what_record_result_would_not_check(
    admin: Connection, factory: Factory
) -> None:
    from sqlalchemy.exc import IntegrityError

    for changes in ({"manifest_digest": "x"}, {"authorization_note": ""}, {"suite_version": 0}):
        with pytest.raises(IntegrityError):
            await record(factory, **changes)
    assert admin.execute(text("SELECT count(*) FROM model_eval_results")).scalar_one() == 0


# --- provider_for_choice ----------------------------------------------------------------------


def test_provider_for_choice_builds_a_catalog_anthropic_model_with_its_key(
    app_database_url: str, kek_dir: Path
) -> None:
    s = worker(app_database_url, kek_dir, "anthropic")
    provider = provider_for_choice(s, ModelChoice("anthropic", OPUS, "medium"))
    assert isinstance(provider, AnthropicProvider)


def test_provider_for_choice_builds_openai_with_its_key_and_needs_it(
    app_database_url: str, kek_dir: Path, catalog_plus: AcceptedEntries
) -> None:
    choice = ModelChoice("openai", catalog_plus.openai.model, "medium")
    built = provider_for_choice(worker(app_database_url, kek_dir, "openai"), choice)
    assert isinstance(built, OpenAIProvider)  # built only; nothing is sent
    (kek_dir / "openai_api_key").unlink()
    with pytest.raises(ProviderUnavailable, match="key file"):
        provider_for_choice(worker(app_database_url, kek_dir, "anthropic"), choice)


@pytest.mark.parametrize(
    ("choice", "rule"),
    [
        (ModelChoice("fake", "fake", "medium"), "catalog"),
        (ModelChoice("anthropic", "fake", "medium"), "catalog"),
        (ModelChoice("anthropic", "claude-opus-9", "medium"), "catalog"),
        (ModelChoice("anthropic", OPUS, "ultra"), "effort"),
        (ModelChoice("deepseek", "deepseek-ok", "medium"), "no adapter"),
    ],
)
def test_provider_for_choice_never_builds_fake_or_a_non_catalog_model(
    app_database_url: str,
    kek_dir: Path,
    catalog_plus: AcceptedEntries,
    choice: ModelChoice,
    rule: str,
) -> None:
    s = worker(app_database_url, kek_dir, "anthropic", "openai", "deepseek")
    with pytest.raises(ProviderUnavailable, match=rule):
        provider_for_choice(s, choice)


def test_provider_for_choice_needs_the_key_and_a_clean_environment(
    app_database_url: str, kek_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ProviderUnavailable, match="key file"):
        provider_for_choice(settings(app_database_url), ModelChoice("anthropic", OPUS, "medium"))
    s = worker(app_database_url, kek_dir, "anthropic")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    with pytest.raises(ProviderUnavailable, match="ANTHROPIC_BASE_URL"):
        provider_for_choice(s, ModelChoice("anthropic", OPUS, "medium"))


# --- the CLI ----------------------------------------------------------------------------------


async def test_the_cli_lists_enables_with_yes_only_and_disables(
    admin: Connection, factory: Factory, app_database_url: str, kek_dir: Path
) -> None:
    s = worker(app_database_url, kek_dir, "anthropic", "openai", "deepseek")
    code, out, err = await models_cli(s, "list")
    assert (code, out.strip()) == (0, "no routes: every job uses the bootstrap model")
    code, out, _ = await models_cli(s, "results")
    assert (code, out.strip()) == (0, "no recorded results")
    result = add_result(admin)
    outputs = [out, err]

    code, out, err = await models_cli(s, "enable", "default", "design", "anthropic", OPUS, "medium")
    outputs += [out, err]
    assert code == 1
    assert f"the route will rely on: {result}" in out
    assert "add --yes" in err
    assert admin.execute(text("SELECT count(*) FROM model_routes")).scalar_one() == 0

    code, out, err = await models_cli(
        s, "enable", "default", "design", "anthropic", OPUS, "medium", "--yes"
    )
    outputs += [out, err]
    assert code == 0, err
    assert "route default design set" in err
    route = admin.execute(text("SELECT set_by, eval_result_id FROM model_routes")).one()
    assert tuple(route) == ("cli", result)

    code, out, err = await models_cli(s, "list")
    outputs += [out, err]
    assert code == 0
    assert out.startswith(f"default design anthropic {OPUS} medium usable result {result}")
    add_result(admin, passed=False)
    code, out, err = await models_cli(s, "list")
    outputs += [out, err]
    assert "unusable: newer_failure stale: newer_failure" in out
    (kek_dir / "anthropic_api_key").write_text("", "utf-8")  # an empty file is no key
    code, out, err = await models_cli(settings(app_database_url), "list")
    outputs += [out, err]
    assert "unusable: no_key" in out

    code, out, err = await models_cli(s, "results", "--provider", "anthropic")
    outputs += [out, err]
    assert code == 0
    assert len(out.strip().splitlines()) == 2
    assert "failed" in out.splitlines()[0]

    code, out, err = await models_cli(s, "enable", "default", "songs", "anthropic", OPUS, "medium")
    outputs += [out, err]
    assert (code, err.strip()) == (1, "refused: no_result")

    code, out, err = await models_cli(s, "disable", "default", "design")
    outputs += [out, err]
    assert code == 0
    code, out, err = await models_cli(s, "disable", "default", "design")
    outputs += [out, err]
    assert (code, err.strip()) == (1, "refused: no_route")
    # No secret in any output: the key files hold the canary.
    assert all(CANARY not in text_ for text_ in outputs)
