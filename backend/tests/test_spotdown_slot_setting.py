"""``CLASSBEAT_SPOTDOWN_MIX_CONCURRENCY``: the cap on running jobs that hold a spotdown slot
(``jobs.spotdown_slot``, #574). #1299 renamed it from ``CLASSBEAT_MIX_ANALYSIS_CONCURRENCY``, with
no fallback: the old name is ignored like any unknown setting and appears in no deploy file."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from classbeat.config import Settings

ROOT = Path(__file__).resolve().parents[2]
OLD_ENV = "CLASSBEAT_MIX_ANALYSIS_CONCURRENCY"
OLD_FIELD = "mix_analysis_concurrency"


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_the_cap_is_read_from_its_spotdown_name_and_defaults_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLASSBEAT_SPOTDOWN_MIX_CONCURRENCY", raising=False)
    assert settings().spotdown_mix_concurrency == 1
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_MIX_CONCURRENCY", "3")
    assert settings().spotdown_mix_concurrency == 3


@pytest.mark.parametrize("value", ["0", "17", "-1"])
def test_the_cap_is_one_to_sixteen_like_queue_claims_own_check(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_MIX_CONCURRENCY", value)
    with pytest.raises(ValidationError, match="spotdown_mix_concurrency"):
        settings()


def test_the_old_name_is_ignored_as_any_unknown_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLASSBEAT_SPOTDOWN_MIX_CONCURRENCY", raising=False)
    monkeypatch.setenv(OLD_ENV, "5")
    loaded = settings()
    assert loaded.spotdown_mix_concurrency == 1  # no fallback to the old name
    assert not hasattr(loaded, OLD_FIELD)
    assert OLD_FIELD not in Settings.model_fields


def _deploy_files() -> list[Path]:
    files = [*ROOT.glob("compose*.yaml"), ROOT / ".env.example", ROOT / "Dockerfile"]
    files += [*(ROOT / "docs" / "runbooks").glob("*")]
    files += [*(ROOT / ".github" / "workflows").glob("*.yml")]
    files += [*(ROOT / "scripts").rglob("*.sh")]
    return [f for f in files if f.is_file()]


def test_the_old_name_is_in_no_compose_env_runbook_workflow_or_script() -> None:
    files = _deploy_files()
    assert ROOT / "compose.prod.yaml" in files
    assert ROOT / "docs" / "runbooks" / "prod.env.example" in files
    for path in files:
        body = path.read_text(encoding="utf-8", errors="replace")
        assert OLD_ENV not in body, path.relative_to(ROOT)
        assert OLD_FIELD.upper() not in body, path.relative_to(ROOT)
