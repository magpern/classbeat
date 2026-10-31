"""M3c "done when", the jobs that use an enabled route (ADR 0012 section 5; issue #219).

After the offline suite run, the record and ``models enable`` of ``test_m3c_done_when``, a design
job and a song job (pick phase on the songs group, design phase on the design group) run on the
route's replayed OpenAI or DeepSeek adapter and record the route key and the result they rest on.
A newer failing record then stops the next job (``model_config``) before any request. Fakes only.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentfake import CONFIG, provider
from classbeat import design
from classbeat.config import Settings
from classbeat.design import Runtime
from classbeat.models.providers import RouteModels
from designtools import DesignEnv, FakeMowlMusic
from jobtools import new_job
from m3ctools import CHOICES, ReplayBuilds, enable_args, owner_says_yes, record, running_suite
from modeltools import add_result, clear_gate, models_cli, settings
from songtools import SongEnv
from test_evals_suite_live import small_suite

CANARY = "sk-canary-not-a-real-key-0123456789abcdef"
PROVIDERS = ["openai", "deepseek"]
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def rec_factory(app_database_url: str) -> AsyncIterator[Factory]:
    engine = create_async_engine(app_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def worker(app_url: str, kek_dir: Path, name: str) -> Settings:
    for key in ("anthropic", name):
        (kek_dir / f"{key}_api_key").write_text(CANARY + "\n", "utf-8")
    return settings(app_url, deepseek_enabled=True)


async def enable_all(
    s: Settings, name: str, groups: tuple[str, ...], tmp_path: Path, rec: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Replay and record a small suite for ``name``, then enable ``groups`` for ``default``."""
    suite, choice = small_suite(), CHOICES[name]
    running_suite(monkeypatch, suite)
    owner_says_yes(monkeypatch, choice)
    done = await record(tmp_path, rec, suite, name)
    assert done.code == 0, (done.out, done.err)
    for group in groups:
        code, _, err = await models_cli(s, *enable_args(choice, group), "--yes")
        assert code == 0, err


@pytest.mark.parametrize("name", PROVIDERS)
async def test_a_design_job_runs_on_the_enabled_route_and_records_it(
    name: str, denv: DesignEnv, fake_mowl: FakeMowlMusic, kek_dir: Path,
    app_database_url: str, rec_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    clear_gate(denv.admin)
    s = worker(app_database_url, kek_dir, name)
    try:
        await enable_all(s, name, ("design",), tmp_path, rec_factory, monkeypatch)
        builds = ReplayBuilds(lambda: provider("good_first_draft"))
        design.configure(
            Runtime(keyring=denv.ops.keyring, models=RouteModels(s, build=builds), agent=CONFIG)
        )
        owner = await denv.ops.user()
        conn = await denv.ops.connect(owner)
        job_id = new_job(denv.admin, owner, kind=design.KIND, input=json.dumps(denv.input(conn)))
        await denv.run_next()

        row = denv.ops.row(job_id)
        assert (row.state, row.error_category) == ("completed", None)
        [chosen] = builds.chosen
        assert (chosen.provider, chosen.model, chosen.route_key) == (
            name,
            CHOICES[name].model,
            "default",
        )
        assert all(api.problems == [] for api in builds.apis)
        [run] = denv.agent_runs(job_id)
        assert (run.provider, run.model, run.route_key) == (name, CHOICES[name].model, "default")
        assert run.eval_result_id == chosen.eval_result_id
        [revision] = denv.revisions(job_id)
        assert revision.provenance["provider"] == name
        assert revision.provenance["eval_result_id"] == str(run.eval_result_id)

        # A newer failing record of the same model: the next job fails before any request.
        add_result(
            denv.admin, name, CHOICES[name].model, CHOICES[name].effort, "design", passed=False
        )
        again = new_job(denv.admin, owner, kind=design.KIND, input=json.dumps(denv.input(conn)))
        before = len(builds.chosen)
        await denv.run_next()
        row = denv.ops.row(again)
        assert (row.state, row.error_category) == ("failed", "model_config")
        assert len(builds.chosen) == before
        assert denv.agent_runs(again) == []
    finally:
        clear_gate(denv.admin)


@pytest.mark.parametrize("name", PROVIDERS)
async def test_a_song_job_runs_both_phases_on_the_enabled_routes(
    name: str, songenv: SongEnv, kek_dir: Path, app_database_url: str,
    rec_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    clear_gate(songenv.admin)
    s = worker(app_database_url, kek_dir, name)
    try:
        await enable_all(s, name, ("songs", "design"), tmp_path, rec_factory, monkeypatch)
        builds = ReplayBuilds(songenv.new_provider)  # the script of the phase being run
        design.configure(
            Runtime(keyring=songenv.ring, models=RouteModels(s, build=builds), agent=CONFIG)
        )
        rider = await songenv.rider()
        job_id, _ = await songenv.approved(rider)  # the pick phase, on the songs route
        row = await songenv.run(job_id)  # the design phase, on the design route
        assert (row.state, row.error_category) == ("completed", None)

        runs = songenv.sql(
            "SELECT stage, provider, model, route_key, eval_result_id FROM agent_runs "
            "WHERE job_id = :j ORDER BY started_at",
            j=job_id,
        ).all()
        assert [r.stage for r in runs] == ["pick_pool", "choose_songs", "design"]
        assert {(r.provider, r.model, r.route_key) for r in runs} == {
            (name, CHOICES[name].model, "default")
        }
        by_group = {
            g: songenv.sql(
                "SELECT id FROM model_eval_results WHERE stage_group = :g", g=g
            ).scalar_one()
            for g in ("songs", "design")
        }
        assert [r.eval_result_id for r in runs] == [
            by_group["songs"], by_group["songs"], by_group["design"]
        ]  # fmt: skip
        assert [c.provider for c in builds.chosen] == [name, name]
        assert all(api.problems == [] for api in builds.apis)
    finally:
        clear_gate(songenv.admin)
