"""Helpers for the M3c done-when test (ADR 0012 section 5, "How the gate is tested"; issue #219).

Everything is offline: the real ``OpenAIProvider`` and ``DeepSeekProvider`` answer from recorded
transcripts on an ``httpx.MockTransport`` (``openaifake.scripted_openai``,
``deepseekfake.scripted_deepseek``), the whole live command (``suite_live.main_suite``) runs a small
suite through them and records the result into the test database, and ``models enable`` and the
worker's ``RouteModels`` then use that record. No key is real and nothing leaves the process.
"""

import asyncio
import io
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.agent import catalog
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers import DeepSeekProvider, FakeProvider, OpenAIProvider
from classbeat.config import Settings
from classbeat.db import system_session
from classbeat.evals import suite_live
from classbeat.evals.suite import Suite
from classbeat.models import routes as routes_module
from classbeat.models.routes import ModelChoice
from deepseekfake import DEEPSEEK_EFFORT, DEEPSEEK_MODEL, scripted_deepseek
from modeltools import owner_accepts
from openaifake import OPENAI_MODEL, scripted_openai
from pickscripts import script_for as song_script
from providerreplay import TEST_KEY
from suitescripts import broken_phrase, good_draft
from suitescripts import provider_for_case as design_provider

NOTE = "owner go-ahead 2026-10-31"
CHOICES = {
    "openai": ModelChoice("openai", OPENAI_MODEL, "high"),
    "deepseek": ModelChoice("deepseek", DEEPSEEK_MODEL, DEEPSEEK_EFFORT),
}


def scripted(provider: str, fake: FakeProvider) -> tuple[ModelProvider, Any]:
    """The real adapter of ``provider`` on a transport that replays ``fake``'s transcript."""
    if provider == "openai":
        return scripted_openai(fake)
    return scripted_deepseek(fake)


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError("the kind check's provider is never asked")


@dataclass
class Transcripts:
    """The provider factory of the offline suite run: the first build is the adapter-kind check,
    then one adapter per run, in ``run_order``, answered by that case's recorded script. A case
    in ``bad`` answers with a broken phrase change, so the run fails its checks."""

    suite: Suite
    provider: str
    bad: frozenset[str] = frozenset()
    apis: list[Any] = field(default_factory=lambda: list[Any]())
    checked: bool = False

    def __post_init__(self) -> None:
        self.queue = suite_live.run_order(self.suite, 1)

    def __call__(self, settings: Settings, choice: ModelChoice) -> ModelProvider:
        assert choice == CHOICES[self.provider]
        if not self.checked:
            self.checked = True
            transport = httpx.MockTransport(_never)
            if self.provider == "openai":
                return OpenAIProvider(SecretStr(TEST_KEY), transport=transport)
            return DeepSeekProvider(SecretStr(TEST_KEY), transport=transport)
        run = self.queue.pop(0)
        if run.group == "songs":
            fake = FakeProvider(song_script(self.suite.song_cases[run.case]))
        else:
            case = self.suite.design_cases[run.case]
            draft = broken_phrase(case, good_draft(case)) if run.case in self.bad else None
            fake = design_provider(case, draft)
        made, api = scripted(self.provider, fake)
        self.apis.append(api)
        return made

    def install(self, choice: ModelChoice) -> None:
        return None


@dataclass(frozen=True)
class Recorded:
    code: int
    out: str
    err: str
    transcripts: Transcripts


async def record(
    tmp_path: Path,
    rec_factory: async_sessionmaker[AsyncSession],
    suite: Suite,
    provider: str,
    *,
    bad: frozenset[str] = frozenset(),
) -> Recorded:
    """The whole live command offline: run ``suite`` through ``provider``'s replayed transcripts,
    write the format-2 file and ``--record`` it (one row per group) as the worker would."""
    choice = CHOICES[provider]
    transcripts = Transcripts(suite, provider, bad)
    out, err = io.StringIO(), io.StringIO()

    def go() -> int:
        return suite_live.main_suite(
            Settings(),
            choice=choice,
            repeat=1,
            max_usd="20",
            out_path=tmp_path / f"{provider}-suite.json",
            record_run=True,
            authorization=NOTE,
            out=out,
            err=err,
            provider_factory=transcripts,
            install=transcripts.install,
            open_session=lambda: system_session(rec_factory),
            suite=suite,
        )

    code = await asyncio.to_thread(go)
    assert all(api.problems == [] for api in transcripts.apis)
    return Recorded(code, out.getvalue(), err.getvalue(), transcripts)


def running_suite(monkeypatch: pytest.MonkeyPatch, suite: Suite) -> None:
    """The gate's "running suite" is ``suite`` (the small one a test ran): its digest is the one a
    result must carry. The real suite's digest then differs from the recorded rows', which is what
    the changed-suite refusal tests."""
    monkeypatch.setattr(routes_module, "running_manifest_digest", lambda: suite.manifest.digest)


def owner_says_yes(monkeypatch: pytest.MonkeyPatch, choice: ModelChoice) -> None:
    """The owner's "yes" to ADR 0012 owner questions 1 or 2, simulated for one test: the
    provider may be ``allowed`` and the catalog entry of ``choice`` is."""
    owner_accepts(monkeypatch, choice.provider)
    entries = tuple(
        replace(e, data_policy="allowed")
        if (e.provider, e.model) == (choice.provider, choice.model)
        else e
        for e in catalog.CATALOG
    )
    monkeypatch.setattr(catalog, "CATALOG", entries)


def enable_args(choice: ModelChoice, group: str, key: str = "default") -> tuple[str, ...]:
    return ("enable", key, group, choice.provider, choice.model, choice.effort)


Builder = Callable[[Settings, ModelChoice], ModelProvider]


@dataclass
class ReplayBuilds:
    """``RouteModels.build`` for the job tests: records every choice and answers the choice's
    real adapter on a transport replaying ``script()``'s transcript."""

    script: Callable[[], FakeProvider]
    chosen: list[ModelChoice] = field(default_factory=lambda: list[ModelChoice]())
    apis: list[Any] = field(default_factory=lambda: list[Any]())

    def __call__(self, settings: Settings, choice: ModelChoice) -> ModelProvider:
        self.chosen.append(choice)
        made, api = scripted(choice.provider, self.script())
        self.apis.append(api)
        return made
