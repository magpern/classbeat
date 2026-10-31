"""Helpers for the enable-gate tests (ADR 0012 section 5; #211): results written as the superuser
(any time, any shape), test catalog entries, settings and the ``models`` CLI."""

import argparse
import io
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import make_settings
from classbeat.agent import catalog
from classbeat.agent.catalog import CatalogModel
from classbeat.cli import add_subcommands
from classbeat.config import Settings
from classbeat.egress import MODEL_HOSTS
from classbeat.models.cli import run
from classbeat.models.versions import (
    running_manifest_digest,
    running_prompt_versions,
    running_suite_version,
)

ZEROS = "0" * 64
OPUS = "claude-opus-5-5"
# Test-only catalog entries for OpenAI and DeepSeek as they ship: ``owner_pending`` (the catalog
# type refuses ``allowed`` for a provider outside ``USER_DATA_PROVIDERS``).
PENDING_OPENAI = CatalogModel("openai", "gpt-test", ("low", "medium"), MODEL_HOSTS["openai"],
                              "owner_pending")  # fmt: skip
DEEPSEEK_PENDING = CatalogModel("deepseek", "deepseek-test", ("medium",), MODEL_HOSTS["deepseek"],
                                "owner_pending")  # fmt: skip


@dataclass(frozen=True)
class AcceptedEntries:
    """``allowed`` OpenAI and DeepSeek entries, made only after the owner's "yes" is simulated."""

    openai: CatalogModel
    deepseek: CatalogModel


def owner_accepts(monkeypatch: pytest.MonkeyPatch, *providers: str) -> None:
    """Simulate, for one test, the owner's "yes" to ADR 0012 owner questions 1 and 2: the reviewed
    change that adds a provider to ``catalog.USER_DATA_PROVIDERS``. The type's check itself is
    unchanged (``test_the_catalog_type_refuses_allowed_for_openai_and_deepseek``)."""
    accepted = frozenset({*catalog.USER_DATA_PROVIDERS, *providers})
    monkeypatch.setattr(catalog, "USER_DATA_PROVIDERS", accepted)


def extend_catalog(monkeypatch: pytest.MonkeyPatch) -> AcceptedEntries:
    """The catalog plus the two ``owner_pending`` entries and, under a simulated owner "yes", an
    ``allowed`` OpenAI and DeepSeek entry (for the paths only an accepted provider reaches)."""
    owner_accepts(monkeypatch, "openai", "deepseek")
    accepted = AcceptedEntries(
        CatalogModel("openai", "gpt-allowed", ("medium",), MODEL_HOSTS["openai"], "allowed"),
        CatalogModel("deepseek", "deepseek-ok", ("medium",), MODEL_HOSTS["deepseek"], "allowed"),
    )
    entries = (PENDING_OPENAI, accepted.openai, DEEPSEEK_PENDING, accepted.deepseek)
    monkeypatch.setattr(catalog, "CATALOG", (*catalog.CATALOG, *entries))
    return accepted


def add_result(
    admin: Connection,
    provider: str = "anthropic",
    model: str = OPUS,
    effort: str = "medium",
    group: str = "design",
    *,
    passed: bool = True,
    suite_version: int | None = None,
    prompt_versions: dict[str, str] | None = None,
    age_minutes: float = 0,
    cost: int = 120_000,
    manifest_digest: str | None = None,
) -> uuid.UUID:
    """A result row inserted as the superuser, ``age_minutes`` before now (setup only); the
    running suite's version and digest unless given."""
    result_id = uuid.uuid4()
    verdict: dict[str, Any] = {"passed": passed, "status": "passed" if passed else "failed"}
    admin.execute(
        text(
            "INSERT INTO model_eval_results (id, run_id, recorded_at, suite_version, "
            "manifest_digest, provider, model, effort, stage_group, prompt_versions, "
            "classbeat_version, results_sha256, verdict, passed, total_cost_micros, "
            "authorization_note) VALUES (:id, :run, :at, :suite, :digest, :provider, :model, "
            ":effort, :group, CAST(:prompts AS jsonb), '0.3.0', :sha, CAST(:verdict AS jsonb), "
            ":passed, :cost, 'owner go-ahead 2026-10-31')"
        ),
        {
            "id": result_id,
            "run": uuid.uuid4(),
            "at": datetime.now(UTC) - timedelta(minutes=age_minutes),
            "suite": running_suite_version() if suite_version is None else suite_version,
            "digest": running_manifest_digest() if manifest_digest is None else manifest_digest,
            "sha": ZEROS,
            "provider": provider,
            "model": model,
            "effort": effort,
            "group": group,
            "prompts": json.dumps(
                running_prompt_versions(group) if prompt_versions is None else prompt_versions
            ),
            "verdict": json.dumps(verdict),
            "passed": passed,
            "cost": cost,
        },
    )
    return result_id


def clear_gate(admin: Connection) -> None:
    # CASCADE: ``agent_runs.eval_result_id`` (migration 0010) references the results.
    admin.execute(text("TRUNCATE model_routes, model_eval_results CASCADE"))


def settings(app_url: str, **overrides: Any) -> Settings:
    return make_settings(app_url, **overrides)


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_subcommands(parser)
    return parser.parse_args(argv)


async def models_cli(s: Settings, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = await run(parse("models", *argv), s, out, err)
    return code, out.getvalue(), err.getvalue()
