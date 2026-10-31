"""M3c "done when", the gate end to end on replayed providers (ADR 0012 section 5; issue #219).

For OpenAI and DeepSeek, offline: the real adapter replays a recorded transcript, the whole live
command (``suite --live ... --record``) runs a small suite through it and records its result, and
the ``models`` CLI enables a route. A model is enabled for a stage group only with a recorded
passing result on the running suite, prompts and version; a failing, stale or changed-suite result
refuses; DeepSeek is refused unless ``CLASSBEAT_DEEPSEEK_ENABLED`` and its ``data_policy`` is
``allowed``; and the database refuses a route row made by hand. Every refusal reason of
``set_route`` is provoked in ``test_every_refusal_reason...``, which fails when a reason is added
without a case there. The jobs that then use a route are in ``test_m3c_done_when_jobs.py``.

OpenAI and DeepSeek ship ``owner_pending`` (ADR 0012 section 6): the enabling runs under a simulated
owner "yes" (``owner_says_yes``), the refusals on the entries as shipped. No network, no real key.
"""

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, get_args

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classbeat.agent import catalog
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.models import RouteRefused, clear_route, set_route
from classbeat.models import routes as routes_module
from classbeat.models.routes import RefusedReason
from jobtools import engine_for
from m3ctools import CHOICES, enable_args, owner_says_yes, record, running_suite
from modeltools import clear_gate, models_cli, settings
from test_evals_suite_live import small_suite

CANARY = "sk-canary-not-a-real-key-0123456789abcdef"
PROVIDERS = ["openai", "deepseek"]
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def rec_factory(app_database_url: str) -> AsyncIterator[Factory]:
    """The recording command's own connection (it runs in a thread, with its own event loop)."""
    engine = create_async_engine(app_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def gate(database_url: str, clean_auth: None) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        clear_gate(connection)
        yield connection
        clear_gate(connection)


def worker(app_url: str, kek_dir: Path, *providers: str, **overrides: Any) -> Settings:
    for name in providers:
        (kek_dir / f"{name}_api_key").write_text(CANARY + "\n", "utf-8")
    return settings(app_url, **overrides)


def routes(admin: Connection) -> list[Any]:
    sql = "SELECT route_key, stage_group, provider, model, eval_result_id FROM model_routes"
    return list(admin.execute(text(sql + " ORDER BY stage_group")).all())


def results(admin: Connection) -> list[Any]:
    sql = "SELECT * FROM model_eval_results ORDER BY stage_group, recorded_at"
    return list(admin.execute(text(sql)).all())


async def refused(s: Settings, provider: str, group: str = "design") -> str:
    code, _, err = await models_cli(s, *enable_args(CHOICES[provider], group), "--yes")
    assert code == 1, err
    return err.strip()


# --- suite, record, enable ----------------------------------------------------------------------


@pytest.mark.parametrize("name", PROVIDERS)
async def test_a_model_is_enabled_only_after_a_recorded_pass(
    name: str, gate: Connection, rec_factory: Factory, kek_dir: Path,
    app_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    choice, suite = CHOICES[name], small_suite()
    running_suite(monkeypatch, suite)
    owner_says_yes(monkeypatch, choice)
    s = worker(app_database_url, kek_dir, name, deepseek_enabled=True)

    assert await refused(s, name) == "refused: no_result"  # nothing recorded yet
    assert routes(gate) == []

    done = await record(tmp_path, rec_factory, suite, name)
    assert done.code == 0, (done.out, done.err)
    rows = results(gate)
    assert [(r.stage_group, r.passed, r.provider, r.model) for r in rows] == [
        ("design", True, name, choice.model),
        ("songs", True, name, choice.model),
    ]
    assert {r.authorization_note for r in rows} == {"owner go-ahead 2026-10-31"}
    code, out, _ = await models_cli(s, "results", "--provider", name)
    assert (code, out.count("passed")) == (0, 2)

    # Without --yes nothing changes; with it the route stands on that result.
    code, out, _ = await models_cli(s, *enable_args(choice, "design"))
    assert (code, routes(gate)) == (1, [])
    assert f"the route will rely on: {rows[0].id}" in out
    code, _, err = await models_cli(s, *enable_args(choice, "design"), "--yes")
    assert code == 0, err
    [route] = routes(gate)
    assert (route.stage_group, route.provider, route.eval_result_id) == ("design", name, rows[0].id)
    code, out, _ = await models_cli(s, "list")
    assert out.startswith(f"default design {name} {choice.model} {choice.effort} usable result")
    assert CANARY not in out


@pytest.mark.parametrize("name", PROVIDERS)
async def test_a_failing_result_makes_enable_refuse_for_that_group_only(
    name: str, gate: Connection, rec_factory: Factory, kek_dir: Path,
    app_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    choice, suite = CHOICES[name], small_suite()
    running_suite(monkeypatch, suite)
    owner_says_yes(monkeypatch, choice)
    s = worker(app_database_url, kek_dir, name, deepseek_enabled=True)

    done = await record(tmp_path, rec_factory, suite, name, bad=frozenset({"sync_easy_30"}))
    assert done.code == 1
    assert "phrase_changes" in done.out
    assert [(r.stage_group, r.passed) for r in results(gate)] == [
        ("design", False),
        ("songs", True),
    ]

    assert await refused(s, name) == "refused: result_failed"
    assert routes(gate) == []
    code, _, err = await models_cli(s, *enable_args(choice, "songs"), "--yes")
    assert code == 0, err  # the songs group has its own passing result
    assert [r.stage_group for r in routes(gate)] == ["songs"]


@pytest.mark.parametrize("name", PROVIDERS)
async def test_a_pass_recorded_on_another_suite_or_for_other_code_refuses(
    name: str, gate: Connection, rec_factory: Factory, kek_dir: Path,
    app_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    choice, suite = CHOICES[name], small_suite()
    owner_says_yes(monkeypatch, choice)
    s = worker(app_database_url, kek_dir, name, deepseek_enabled=True)
    assert (await record(tmp_path, rec_factory, suite, name)).code == 0

    # The small suite is not the running suite: its digest is not the released one's.
    assert await refused(s, name) == "refused: manifest_digest"
    running_suite(monkeypatch, suite)
    with monkeypatch.context() as later:  # a newer suite version since the pass
        later.setattr(routes_module, "running_suite_version", lambda: 99)
        assert await refused(s, name) == "refused: suite_version"
    with monkeypatch.context() as deployed:  # a prompt or tool schema changed since the pass
        deployed.setattr(routes_module, "running_prompt_versions", lambda g: {"design": "d" * 64})
        assert await refused(s, name) == "refused: prompt_versions"
    assert routes(gate) == []
    code, _, err = await models_cli(s, *enable_args(choice, "design"), "--yes")
    assert code == 0, err  # the same record enables once the suite and code are the ones it ran


async def test_deepseek_is_refused_unless_enabled_and_allowed(
    gate: Connection, rec_factory: Factory, kek_dir: Path, app_database_url: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    choice, suite = CHOICES["deepseek"], small_suite()
    running_suite(monkeypatch, suite)
    assert (await record(tmp_path, rec_factory, suite, "deepseek")).code == 0  # a pass: not enough
    off = worker(app_database_url, kek_dir, "deepseek", deepseek_enabled=False)
    on = settings(app_database_url, deepseek_enabled=True)

    assert await refused(off, "deepseek") == "refused: deepseek_disabled"
    assert await refused(on, "deepseek") == "refused: data_policy"  # on, still owner_pending
    owner_says_yes(monkeypatch, choice)
    assert await refused(off, "deepseek") == "refused: deepseek_disabled"  # allowed, still off
    assert routes(gate) == []
    code, _, err = await models_cli(on, *enable_args(choice, "design"), "--yes")
    assert code == 0, err


async def test_openai_is_refused_while_owner_pending(
    gate: Connection, rec_factory: Factory, kek_dir: Path, app_database_url: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    suite = small_suite()
    running_suite(monkeypatch, suite)
    assert (await record(tmp_path, rec_factory, suite, "openai")).code == 0
    s = worker(app_database_url, kek_dir, "openai")
    for group in ("design", "songs"):
        assert await refused(s, "openai", group) == "refused: data_policy"
    assert routes(gate) == []


# --- no route without a recorded pass: every refusal reason ------------------------------------


async def test_every_refusal_reason_of_set_route_is_tried_and_none_sets_a_route(
    gate: Connection, rec_factory: Factory, kek_dir: Path, app_database_url: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """One recorded OpenAI pass (design and songs), then one provocation per reason code of
    ``RouteRefused``; after each, the route table is empty. The control at the end sets the route
    from the same record, so each refusal was down to its reason alone."""
    choice, suite = CHOICES["openai"], small_suite()
    running_suite(monkeypatch, suite)
    owner_says_yes(monkeypatch, choice)
    assert (await record(tmp_path, rec_factory, suite, "openai")).code == 0
    s = worker(app_database_url, kek_dir, "openai")
    model, effort, tried = choice.model, choice.effort, set[str]()

    async def attempt(reason: str, *args: Any, **kw: Any) -> None:
        tried.add(reason)
        given = args if args else ("default", "design", "openai", model, effort, "cli")
        async with system_session(rec_factory) as db:
            with pytest.raises(RouteRefused) as raised:
                await set_route(db, *given, settings=kw.pop("settings", s))
        assert raised.value.reason == reason, raised.value.reason
        assert routes(gate) == [], reason

    await attempt("invalid_route_key", "Not A Key", "design", "openai", model, effort, "cli")
    await attempt("invalid_stage_group", "default", "ensemble", "openai", model, effort, "cli")
    await attempt("invalid_actor", "default", "design", "openai", model, effort, "")
    await attempt("not_in_catalog", "default", "design", "openai", "gpt-nope", effort, "cli")
    await attempt("effort_not_in_catalog", "default", "design", "openai", model, "ultra", "cli")
    await attempt("no_result", "default", "design", "openai", model, "medium", "cli")  # not run
    await attempt("deepseek_disabled", "default", "design", "deepseek",
                  CHOICES["deepseek"].model, "high", "cli")  # fmt: skip
    with monkeypatch.context() as shipped:  # the entry as it ships: owner_pending
        pending = tuple(
            replace(e, data_policy="owner_pending") if e.model == model else e
            for e in catalog.CATALOG
        )
        shipped.setattr(catalog, "CATALOG", pending)
        await attempt("data_policy")
    with monkeypatch.context() as later:
        later.setattr(routes_module, "running_suite_version", lambda: 99)
        await attempt("suite_version")
    with monkeypatch.context() as changed:
        changed.setattr(routes_module, "running_manifest_digest", lambda: "a" * 64)
        await attempt("manifest_digest")
    with monkeypatch.context() as deployed:
        deployed.setattr(routes_module, "running_prompt_versions", lambda g: {"design": "d" * 64})
        await attempt("prompt_versions")

    # result_failed: a newer failing record of the same model.
    bad = await record(tmp_path, rec_factory, suite, "openai", bad=frozenset({"sync_easy_30"}))
    assert bad.code == 1
    await attempt("result_failed")

    # foreign_key: the database refuses what the check let through (a result that is not a pass).
    [failing] = [r for r in results(gate) if not r.passed]
    tried.add("foreign_key")
    monkeypatch.setattr(routes_module, "gate_result", _stale_read(rec_factory, failing.id))
    async with system_session(rec_factory) as db:
        with pytest.raises(RouteRefused) as raised:
            await set_route(db, "default", "design", "openai", model, effort, "cli", settings=s)
    assert raised.value.reason == "foreign_key"
    assert routes(gate) == []

    # no_route: clearing what is not there.
    tried.add("no_route")
    async with system_session(rec_factory) as db:
        with pytest.raises(RouteRefused) as raised:
            await clear_route(db, "default", "design", "cli")
    assert raised.value.reason == "no_route"

    assert tried == set(get_args(RefusedReason))


def _stale_read(factory: Factory, result_id: uuid.UUID) -> Callable[..., Any]:
    """A ``gate_result`` that hands back a failing result, as a read made before a newer record
    would (the check passed, the write follows): only the database stops it."""

    async def read(db: AsyncSession, *args: Any, **kw: Any) -> Any:
        from classbeat.models import store

        return await store.result_of(db, result_id)

    return read


# --- the database refuses a route made by hand ---------------------------------------------------

ROUTE = (
    "INSERT INTO model_routes (route_key, stage_group, provider, model, effort, eval_result_id, "
    "gate_passed, set_by) VALUES ('default', :group, :provider, :model, :effort, :result, "
    ":passed, 'by hand')"
)


async def test_the_database_refuses_a_hand_made_route_row_and_any_change_to_a_result(
    gate: Connection, rec_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    suite = small_suite()
    assert (await record(tmp_path, rec_factory, suite, "openai")).code == 0
    bad = await record(tmp_path, rec_factory, suite, "openai", bad=frozenset({"sync_easy_30"}))
    assert bad.code == 1
    by_group = {
        (r.stage_group, r.passed): r for r in results(gate)
    }  # design pass, design fail, two songs passes
    good, failing = by_group[("design", True)], by_group[("design", False)]
    base = {"group": "design", "provider": "openai", "model": good.model, "effort": good.effort}
    cases: dict[str, dict[str, Any]] = {
        "a failing result": {**base, "result": failing.id, "passed": True},
        "a failing result claimed as one": {**base, "result": failing.id, "passed": False},
        "an id that is no result": {**base, "result": uuid.uuid4(), "passed": True},
        "another model": {**base, "result": good.id, "passed": True, "model": "gpt-6-luna"},
        "another effort": {**base, "result": good.id, "passed": True, "effort": "low"},
        "another provider": {**base, "result": good.id, "passed": True, "provider": "deepseek"},
        "another group": {**base, "result": good.id, "passed": True, "group": "songs"},
    }
    for label, params in cases.items():
        async with rec_factory() as db:
            with pytest.raises(IntegrityError):
                await db.execute(text(ROUTE), params)
            await db.rollback()
        assert routes(gate) == [], label

    # The same row at the right pass is what the database accepts (so the refusals are the gate's).
    async with rec_factory() as db:
        await db.execute(text(ROUTE), {**base, "result": good.id, "passed": True})
        await db.commit()
    assert len(routes(gate)) == 1

    # Results are append-only for the app role.
    for statement in (
        "UPDATE model_eval_results SET passed = true, verdict = '{\"passed\": true}'",
        "DELETE FROM model_eval_results",
        "TRUNCATE model_eval_results",
    ):
        async with rec_factory() as db:
            with pytest.raises(DBAPIError):
                await db.execute(text(statement))
            await db.rollback()
    assert [r.passed for r in results(gate)] == [True, False, True, True]
