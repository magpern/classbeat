"""``python -m classbeat.evals suite --live`` (ADR 0012 §4.4, §5; M3c EV5, issue #213). Offline:
nothing contacts a provider, no real key exists anywhere.

- The CLI entry refuses (exit 2, no key in any output) without ``--live``, under ``CI``, for a
  model or effort outside the catalog, for ``fake``, without the key file and with a refused SDK
  variable; and it wires exactly the real provider factory and allowlist installer.
- ``main_suite`` prints its plan and stops without ``--max-usd``; installs the allowlist before it
  builds the provider; refuses a provider whose adapter class is not the choice's (a ``fake``
  calling itself ``openai`` included).
- The whole command offline: the real ``OpenAIProvider`` on the scripted Responses transport runs
  a small suite (both groups), writes format 2 and records one row per group: a good fixture set
  gives passing rows, a bad one a failing design row. The verdict is computed against the
  digest-verified manifest the run executed, never one from the file.
"""

import copy
import io
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers import FakeProvider, OpenAIProvider
from classbeat.agent.providers.fake import FakeScript
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.evals import __main__ as evals_main
from classbeat.evals import suite_live
from classbeat.evals.gate import RESULTS_FORMAT_2
from classbeat.evals.suite import (
    Manifest,
    Suite,
    check_constants,
    digest_sources,
    load_suite,
    read_manifest_data,
    suite_digest,
)
from classbeat.models.providers import provider_for_choice
from classbeat.models.routes import ModelChoice
from classbeat.models.store import file_sha256
from classbeat.models.versions import running_prompt_versions
from jobtools import engine_for
from modeltools import clear_gate
from openaifake import OPENAI_MODEL, ScriptedResponses, scripted_openai
from pickscripts import script_for as song_script
from providerreplay import TEST_KEY
from suitescripts import broken_phrase, good_draft
from suitescripts import provider_for_case as design_provider

CHOICE = ModelChoice("openai", OPENAI_MODEL, "high")
CLI = ["suite", "--live", "--provider", "openai", "--model", OPENAI_MODEL, "--effort", "high"]
PLANTED = "sk-planted-test-key-never-printed"
SMALL = {"standard": ("design", "std_easy_20"), "music_sync": ("design", "sync_easy_30"),
         "song_choice": ("songs", "pick_30")}  # fmt: skip


@pytest.fixture(autouse=True)
def not_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)


@pytest.fixture
def key(kek_dir: Path) -> None:
    (kek_dir / "openai_api_key").write_text(PLANTED, "utf-8")


def small_suite() -> Suite:
    """A digest-consistent suite of three cases (one run each), built as ``load_suite`` builds
    the real one: the same case models, sources and constants, its own manifest and digest."""
    full = load_suite()
    data = copy.deepcopy(read_manifest_data())
    data["rules"]["repeat"] = 1
    data["categories"] = {category: {} for category in data["categories"]}
    for category, (group, name) in SMALL.items():
        data["categories"][category] = {group: [name]}
    kept = {name for _, name in SMALL.values()}
    data["expected_outcomes"] = {
        n: o for n, o in data.get("expected_outcomes", {}).items() if n in kept
    }
    design = {n: full.design_cases[n] for _, (g, n) in SMALL.items() if g == "design"}
    songs = {n: full.song_cases[n] for _, (g, n) in SMALL.items() if g == "songs"}
    dumped = {n: c.model_dump(mode="json") for n, c in {**design, **songs}.items()}
    data["digest"] = suite_digest(data, dumped, digest_sources(), check_constants())
    return Suite(Manifest.model_validate(data), design, songs)


class Replays:
    """The provider factory of the offline run: the first build is the kind check, then one
    ``OpenAIProvider`` per run, in ``run_order``, answered by that case's recorded script."""

    def __init__(
        self,
        suite: Suite,
        *,
        bad: frozenset[str] = frozenset(),
        repeat: int = 1,
        cut: dict[suite_live.Run, int] | None = None,
        swap: suite_live.Run | None = None,
    ) -> None:
        self.queue = suite_live.run_order(suite, repeat)
        self.suite = suite
        self.bad = bad
        self.cut = cut or {}  # a run whose song script stops after that many turns
        self.swap = swap  # the run built as another adapter
        self.apis: list[ScriptedResponses] = []
        self.events: list[str] = []
        self.checked = False

    def __call__(self, settings: Settings, choice: ModelChoice) -> ModelProvider:
        self.events.append("build")
        assert choice == CHOICE
        if not self.checked:
            self.checked = True
            return OpenAIProvider(SecretStr(TEST_KEY), transport=httpx.MockTransport(_never))
        run = self.queue.pop(0)
        if run == self.swap:
            return NamedOpenAI.from_file(
                Path(__file__).parent / "agent_scripts" / "good_first_draft.json"
            )
        if run.group == "songs":
            script = song_script(self.suite.song_cases[run.case])
            if run in self.cut:
                script = FakeScript.model_validate(
                    {"model": script.model, "turns": script.turns[: self.cut[run]]}
                )
            fake = FakeProvider(script)
        else:
            case = self.suite.design_cases[run.case]
            draft = broken_phrase(case, good_draft(case)) if run.case in self.bad else None
            fake = design_provider(case, draft)
        provider, api = scripted_openai(fake)
        self.apis.append(api)
        return provider

    def install(self, choice: ModelChoice) -> None:
        self.events.append("install")


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError("the kind check's provider is never asked")


def suite_run(
    tmp_path: Path,
    factory: Callable[[Settings, ModelChoice], ModelProvider],
    install: Callable[[ModelChoice], object],
    *,
    suite: Suite | None = None,
    open_session: Any = None,
    record: bool = False,
    max_usd: str | None = "20",
    repeat: int = 1,
) -> tuple[int, str, str, Path]:
    out, err, path = io.StringIO(), io.StringIO(), tmp_path / "suite.json"
    code = suite_live.main_suite(
        Settings(),
        choice=CHOICE,
        repeat=repeat,
        max_usd=max_usd,
        out_path=path,
        record_run=record,
        authorization="owner go-ahead 2026-10-31" if record else None,
        out=out,
        err=err,
        provider_factory=factory,
        install=install,
        open_session=open_session or _no_session,
        suite=suite if suite is not None else small_suite(),
    )
    return code, out.getvalue(), err.getvalue(), path


def _no_session() -> Any:
    raise AssertionError("no database without --record")


# --- the CLI entry --------------------------------------------------------------------------


@pytest.fixture
def entry(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """``main_suite`` replaced by a spy: what the CLI entry passed, if it got that far."""
    calls: list[dict[str, Any]] = []

    def spy(settings: Settings, **kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(suite_live, "main_suite", spy)
    return calls


def cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = evals_main.main([*argv, "--out", "unused.json"])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


@pytest.mark.usefixtures("key")
@pytest.mark.parametrize(
    ("argv", "rule"),
    [
        ([a for a in CLI if a != "--live"], "pass --live"),
        (["suite", "--live", "--provider", "fake", "--model", "fake", "--effort", "high"],
         "not in the catalog"),
        (["suite", "--live", "--provider", "openai", "--model", "gpt-9", "--effort", "high"],
         "not in the catalog"),
        (["suite", "--live", "--provider", "openai", "--model", "claude-opus-5-5",
          "--effort", "high"], "not in the catalog"),
        ([*CLI[:-1], "ultra"], "does not list that effort"),
    ],
)  # fmt: skip
def test_the_cli_refuses_before_anything_runs(
    capsys: pytest.CaptureFixture[str], entry: list[dict[str, Any]], argv: list[str], rule: str
) -> None:
    code, output = cli(capsys, *argv)
    assert (code, entry) == (2, [])
    assert rule in output
    assert PLANTED not in output


def test_the_cli_refuses_under_ci_even_if_empty(
    capsys: pytest.CaptureFixture[str],
    entry: list[dict[str, Any]],
    key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI", "")
    code, output = cli(capsys, *CLI)
    assert (code, entry) == (2, [])
    assert "CI is set" in output


def test_the_cli_refuses_without_the_key_file(
    capsys: pytest.CaptureFixture[str], entry: list[dict[str, Any]], kek_dir: Path
) -> None:
    code, output = cli(capsys, *CLI)
    assert (code, entry) == (2, [])
    assert "provider openai needs its key file" in output


@pytest.mark.usefixtures("key")
@pytest.mark.parametrize(
    "name", ["OPENAI_BASE_URL", "OPENAI_LOG", "DEEPSEEK_API_KEY", "SSLKEYLOGFILE"]
)
def test_the_cli_refuses_a_refused_sdk_variable(
    capsys: pytest.CaptureFixture[str],
    entry: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv(name, "https://example.invalid")
    code, output = cli(capsys, *CLI)
    assert (code, entry) == (2, [])
    assert name in output
    assert "example.invalid" not in output


@pytest.mark.usefixtures("key")
def test_the_cli_wires_exactly_the_real_factory_and_installer(
    capsys: pytest.CaptureFixture[str], entry: list[dict[str, Any]]
) -> None:
    code, _ = cli(capsys, *CLI, "--repeat", "3", "--max-usd", "40", "--record",
                  "--authorization", "go")  # fmt: skip
    assert code == 0
    (call,) = entry
    assert call["provider_factory"] is provider_for_choice
    assert call["install"] is suite_live.install_choice_allowlist
    assert "suite" not in call  # the digest-verified load_suite(), never one from an argument
    assert callable(call["open_session"])
    assert call["choice"] == CHOICE
    assert (call["repeat"], call["max_usd"], call["record_run"], call["authorization"]) == (
        3,
        "40",
        True,
        "go",
    )


def test_the_real_installer_lists_only_the_chosen_model_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(suite_live, "install_worker_allowlist", lambda p: seen.append(tuple(p)))
    suite_live.install_choice_allowlist(CHOICE)
    assert seen == [("openai",)]


# --- main_suite before the runs ---------------------------------------------------------------


def test_it_prints_the_plan_and_ceiling_then_stops_without_max_usd(tmp_path: Path) -> None:
    replays = Replays(small_suite())
    code, out, err, path = suite_run(tmp_path, replays, replays.install, max_usd=None)
    assert code == 2
    assert "LIVE: provider openai, model gpt-6.1-sol, effort high" in out
    assert "2 design cases and 1 song cases, 3 runs" in out
    assert "estimated cost ceiling for this run: $" in out
    assert "pass --max-usd" in err
    assert (replays.events, path.exists()) == ([], False)


@pytest.mark.parametrize(
    ("args", "rule"),
    [({"record_run": True, "authorization": None}, "--record needs --authorization"),
     ({"record_run": True, "authorization": " "}, "--record needs --authorization"),
     ({"record_run": True, "authorization": "x" * 201}, "--record needs --authorization"),
     ({"record_run": False, "authorization": "go"}, "only for --record"),
     ({"repeat": 11}, "--repeat"), ({"max_usd": "0"}, "--max-usd"),
     ({"max_usd": "nan"}, "--max-usd")],
)  # fmt: skip
def test_it_refuses_bad_arguments_before_anything_runs(
    tmp_path: Path, args: dict[str, Any], rule: str
) -> None:
    replays = Replays(small_suite())
    err = io.StringIO()
    values: dict[str, Any] = {"repeat": 1, "max_usd": "20", "record_run": False,
                              "authorization": None, **args}  # fmt: skip
    code = suite_live.main_suite(
        Settings(),
        choice=CHOICE,
        out_path=tmp_path / "x.json",
        out=io.StringIO(),
        err=err,
        provider_factory=replays,
        install=replays.install,
        open_session=_no_session,
        suite=small_suite(),
        **values,
    )
    assert (code, replays.events) == (2, [])
    assert rule in err.getvalue()


class NamedOpenAI(FakeProvider):
    name = "openai"  # what it calls itself is not what it is


@pytest.mark.parametrize("record", [False, True])
def test_a_provider_that_is_not_the_choices_adapter_is_refused(
    tmp_path: Path, record: bool
) -> None:
    events: list[str] = []
    fake = NamedOpenAI.from_file(Path(__file__).parent / "agent_scripts" / "good_first_draft.json")

    def factory(settings: Settings, choice: ModelChoice) -> ModelProvider:
        events.append("build")
        return fake

    code, _, err, path = suite_run(
        tmp_path, factory, lambda c: events.append("install"), record=record
    )
    assert code == 2
    assert "not the chosen provider's adapter" in err
    assert (events, path.exists()) == (["install", "build"], False)  # the list came first


def test_the_provider_kind_is_the_adapter_class() -> None:
    real = OpenAIProvider(SecretStr(TEST_KEY), transport=httpx.MockTransport(_never))
    assert suite_live.provider_kind(real) == "openai"
    assert suite_live.provider_kind(NamedOpenAI.from_file(
        Path(__file__).parent / "agent_scripts" / "good_first_draft.json")) is None  # fmt: skip


# --- the whole command offline --------------------------------------------------------------


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
async def factory(app_database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(app_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def stored(admin: Connection) -> list[Any]:
    return list(admin.execute(text("SELECT * FROM model_eval_results ORDER BY stage_group")).all())


def _run_offline(
    tmp_path: Path,
    factory: async_sessionmaker[AsyncSession],
    replays: Replays,
    suite: Suite,
    repeat: int = 1,
) -> tuple[int, str, str, Path]:
    return suite_run(
        tmp_path,
        replays,
        replays.install,
        suite=suite,
        record=True,
        open_session=lambda: system_session(factory),
        repeat=repeat,
    )


async def _in_thread(fn: Callable[..., Any], *args: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args)


async def test_a_good_fixture_set_writes_format_2_and_records_passing_rows(
    tmp_path: Path, admin: Connection, factory: async_sessionmaker[AsyncSession]
) -> None:
    suite = small_suite()
    replays = Replays(suite)
    code, out, err, path = await _in_thread(_run_offline, tmp_path, factory, replays, suite)
    assert code == 0, (out, err)
    assert replays.events[:2] == ["install", "build"]
    assert all(api.problems == [] for api in replays.apis)
    assert len(replays.apis) == 3
    assert replays.queue == []
    document = path.read_bytes()
    doc = json.loads(document)
    assert doc["results_format"] == RESULTS_FORMAT_2
    assert (doc["provider"], doc["model"], doc["effort"]) == ("openai", OPENAI_MODEL, "high")
    assert (doc["suite_version"], doc["manifest_digest"]) == (
        suite.manifest.suite_version,
        suite.manifest.digest,
    )
    assert doc["provider_kind"] == "openai"
    assert sorted(r["case"] for r in doc["results"]) == ["pick_30", "std_easy_20", "sync_easy_30"]
    assert PLANTED not in out + err + document.decode()
    rows = stored(admin)
    assert [(r.stage_group, r.passed) for r in rows] == [("design", True), ("songs", True)]
    for row in rows:
        assert (row.provider, row.model, row.effort) == ("openai", OPENAI_MODEL, "high")
        assert row.results_sha256 == file_sha256(document)
        assert (row.suite_version, row.manifest_digest) == (
            suite.manifest.suite_version,
            suite.manifest.digest,
        )
        assert row.prompt_versions == running_prompt_versions(row.stage_group)
        assert row.verdict["passed"] is True
        assert row.authorization_note == "owner go-ahead 2026-10-31"
        group_cost = sum(
            r["cost_micros"]
            for r in doc["results"]
            if suite.manifest.case_index()[r["case"]][1] == row.stage_group
        )
        assert row.total_cost_micros == group_cost
    assert len({r.run_id for r in rows}) == 1
    assert f"recorded result {rows[0].id}" in out


async def test_a_bad_fixture_set_records_a_failing_row(
    tmp_path: Path, admin: Connection, factory: async_sessionmaker[AsyncSession]
) -> None:
    suite = small_suite()
    replays = Replays(suite, bad=frozenset({"sync_easy_30"}))
    code, out, _, _ = await _in_thread(_run_offline, tmp_path, factory, replays, suite)
    assert code == 1
    rows = stored(admin)
    assert [(r.stage_group, r.passed) for r in rows] == [("design", False), ("songs", True)]
    design = rows[0].verdict
    assert design["status"] == "failed"
    assert "phrase_changes" in out


async def test_the_verdict_is_computed_against_the_suite_that_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``suite`` left out: ``main_suite`` loads the digest-verified suite itself, and that
    manifest (the same object) is what the verdict is computed against."""
    suite = small_suite()
    loads: list[Suite] = []
    seen: list[Manifest] = []
    real_evaluate = suite_live.evaluate

    def load() -> Suite:
        loads.append(suite)
        return suite

    def evaluate(results: Any, manifest: Manifest) -> Any:
        seen.append(manifest)
        return real_evaluate(results, manifest)

    monkeypatch.setattr(suite_live, "load_suite", load)
    monkeypatch.setattr(suite_live, "evaluate", evaluate)
    replays = Replays(suite)
    out, err = io.StringIO(), io.StringIO()
    code = await _in_thread(
        lambda: suite_live.main_suite(
            Settings(),
            choice=CHOICE,
            repeat=1,
            max_usd="20",
            out_path=tmp_path / "s.json",
            out=out,
            err=err,
            provider_factory=replays,
            install=replays.install,
            open_session=_no_session,
        )
    )
    assert code == 0, err.getvalue()
    assert loads == [suite]
    assert len(seen) == 1
    assert seen[0] is suite.manifest


# --- a failed run still costs what it spent --------------------------------------------------


async def test_a_song_run_that_fails_after_billed_turns_still_uses_budget_and_is_recorded(
    tmp_path: Path,
    admin: Connection,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pick_30's first run has its script cut short after five answered requests (the real
    ``OpenAIProvider`` on the scripted transport): it fails, and what it spent goes off the
    budget the next run gets and into the recorded songs cost (ADR 0006 §5: cost errs high)."""
    suite = small_suite()
    failing = suite_live.Run("songs", "pick_30", 0)
    replays = Replays(suite, repeat=2, cut={failing: 5})
    seen: list[tuple[suite_live.Run, int, int]] = []
    real_one = suite_live._one  # pyright: ignore[reportPrivateUsage]

    async def one(run: suite_live.Run, *args: Any) -> Any:
        left = args[3]  # (suite, provider, budgets, left, choice)
        result = await real_one(run, *args)
        seen.append((run, left, result[1]))
        return result

    monkeypatch.setattr(suite_live, "_one", one)
    _, out, err, path = await _in_thread(_run_offline, tmp_path, factory, replays, suite, 2)
    assert replays.queue == [], (out, err)
    doc = json.loads(path.read_bytes())
    first = next(r for r in doc["results"] if (r["case"], r["repeat"]) == ("pick_30", 0))
    assert first["outcome"] != "submitted"
    assert first["cost_micros"] > 0
    assert first["turns"] > 0
    runs = [run for run, _, _ in seen]
    at = runs.index(failing)
    _, left, spent = seen[at]
    assert spent == first["cost_micros"]
    assert seen[at + 1][1] == left - spent  # the next run's budget went down by it
    songs = next(r for r in stored(admin) if r.stage_group == "songs")
    song_costs = [r["cost_micros"] for r in doc["results"] if r["case"] == "pick_30"]
    assert songs.total_cost_micros == sum(song_costs)
    assert songs.total_cost_micros > song_costs[1]
    assert songs.passed is False


async def test_a_song_run_whose_spend_is_unknown_is_charged_what_was_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = small_suite()

    async def broken(*args: Any) -> Any:
        raise RuntimeError("request data that must not be shown")

    monkeypatch.setattr(suite_live, "run_pick_case", broken)
    budgets = suite_live.Budgets.build(Settings(), CHOICE)
    row, spent, passed, _ = await suite_live._one(  # pyright: ignore[reportPrivateUsage]
        suite_live.Run("songs", "pick_30", 0), suite, FakeProvider.from_file(
            Path(__file__).parent / "agent_scripts" / "good_first_draft.json"),
        budgets, 1_234_567, CHOICE,
    )  # fmt: skip
    assert (spent, row["cost_micros"], passed) == (1_234_567, 1_234_567, False)
    assert row["outcome"] == "error:RuntimeError"
    assert "request data" not in json.dumps(row)


@pytest.mark.parametrize("record", [False, True])
def test_every_build_is_checked_not_only_the_first(tmp_path: Path, record: bool) -> None:
    suite = small_suite()
    replays = Replays(suite, swap=suite_live.run_order(suite, 1)[1])
    code, _, err, path = suite_run(tmp_path, replays, replays.install, suite=suite, record=record)
    assert code == 2
    assert "not the chosen provider's adapter" in err
    assert not path.exists()  # nothing written, nothing recorded (no session is opened)
