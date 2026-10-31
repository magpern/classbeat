"""The OpenAI and DeepSeek key files and the refused SDK environment (ADR 0012 §2).

The keys come only from their secret files (worker only, empty = none, masked), the web app
refuses to start when it can read any model key, the worker refuses ``ANTHROPIC_*``,
``OPENAI_*``, ``DEEPSEEK_*`` and ``SSLKEYLOGFILE`` with a real provider, and
``CLASSBEAT_MODEL_PROVIDER`` stays ``anthropic | fake``."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from classbeat.agent.providers import ProviderUnavailable, provider_for
from classbeat.config import (
    MODEL_KEY_FIELDS,
    ModelProviderRefused,
    Settings,
    check_web_has_no_model_key,
    check_worker_model_provider,
    model_sdk_environment_problems,
)
from classbeat.web import create_app

PLANTED = "sk-planted-test-value-0123456789abcdef"
NEW_KEYS = ["openai_api_key", "deepseek_api_key"]


def _write(directory: Path, field: str, value: str | bytes) -> None:
    data = value.encode() if isinstance(value, str) else value
    (directory / field).write_bytes(data)


def test_every_provider_has_its_own_key_file() -> None:
    assert MODEL_KEY_FIELDS == {
        "anthropic": "anthropic_api_key",
        "openai": "openai_api_key",
        "deepseek": "deepseek_api_key",
    }


@pytest.mark.parametrize("field", NEW_KEYS)
def test_the_key_comes_from_its_secret_file(kek_dir: Path, field: str) -> None:
    _write(kek_dir, field, PLANTED + "\n")
    key = getattr(Settings(), field)
    assert key is not None
    assert key.get_secret_value() == PLANTED


@pytest.mark.parametrize("field", NEW_KEYS)
def test_the_key_is_never_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv(f"CLASSBEAT_{field.upper()}", PLANTED)
    monkeypatch.setenv(field.upper(), PLANTED)
    assert getattr(Settings(), field) is None


@pytest.mark.parametrize("field", NEW_KEYS)
@pytest.mark.parametrize("content", ["", "\n", "   ", " \r\n"])
def test_an_empty_placeholder_file_is_no_key(kek_dir: Path, field: str, content: str) -> None:
    _write(kek_dir, field, content)
    assert getattr(Settings(), field) is None


@pytest.mark.parametrize("field", NEW_KEYS)
@pytest.mark.parametrize("bad", ["sk two", "sk\ttab", "sk\x00nul", "sk-é"])
def test_a_key_with_whitespace_or_control_characters_is_refused_without_echo(
    kek_dir: Path, field: str, bad: str
) -> None:
    _write(kek_dir, field, bad)
    with pytest.raises(ValueError, match=field) as raised:
        Settings()
    assert bad not in str(raised.value)


@pytest.mark.parametrize("field", NEW_KEYS)
def test_the_key_is_masked(kek_dir: Path, field: str) -> None:
    _write(kek_dir, field, PLANTED)
    settings = Settings()
    for text in (repr(settings), str(settings), str(settings.model_dump())):
        assert PLANTED not in text
    assert PLANTED not in settings.model_dump_json()


@pytest.mark.parametrize("field", NEW_KEYS)
def test_the_web_app_refuses_to_start_when_it_can_read_the_key(kek_dir: Path, field: str) -> None:
    _write(kek_dir, field, PLANTED)
    with pytest.raises(ModelProviderRefused, match=field) as raised:
        create_app(Settings())
    assert PLANTED not in str(raised.value)


def test_the_web_app_starts_with_every_placeholder_empty(kek_dir: Path) -> None:
    for field in MODEL_KEY_FIELDS.values():
        _write(kek_dir, field, "")
    check_web_has_no_model_key(Settings())
    create_app(Settings())


@pytest.mark.parametrize("value", ["openai", "deepseek", "OpenAI", "Anthropic"])
def test_the_model_provider_setting_stays_anthropic_or_fake(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CLASSBEAT_MODEL_PROVIDER", value)
    with pytest.raises(ValueError, match="model_provider"):
        Settings()


def test_deepseek_is_off_by_default_and_switched_by_its_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings().deepseek_enabled is False
    monkeypatch.setenv("CLASSBEAT_DEEPSEEK_ENABLED", "true")
    assert Settings().deepseek_enabled is True


# --- the refused SDK environment ----------------------------------------------------------

REFUSED = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_LOG",
    "ANTHROPIC_CUSTOM_HEADERS",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_LOG",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "SSLKEYLOGFILE",
]


@pytest.mark.parametrize("name", REFUSED)
def test_the_variable_is_named_never_its_value(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "planted-secret-value")
    problems = model_sdk_environment_problems()
    assert name in problems
    assert "planted-secret-value" not in str(problems)


@pytest.mark.parametrize(
    "name",
    ["openai_api_key", "Anthropic_Log", "deepseek_base_url", "sslkeylogfile", "OpenAI_Org_Id"],
)
def test_a_name_in_any_case_is_refused(name: str) -> None:
    """Linux environment names are case-sensitive and the SDKs only read upper case, but a name
    in another case is refused too (a mistyped or tool-written variable is a leak path all the
    same; the #238 review). The environment is passed in, so this holds on Windows as well."""
    assert model_sdk_environment_problems({name: "planted", "PATH": "/bin"}) == [name]


def test_the_refusal_reads_the_process_environment_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_LOG", "debug")
    assert model_sdk_environment_problems() == ["OPENAI_LOG"]


@pytest.mark.parametrize("name", ["CLASSBEAT_DEEPSEEK_ENABLED", "MY_OPENAI_NOTE", "NOT_ANTHROPIC"])
def test_other_variables_are_not_refused(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "x")
    assert name not in model_sdk_environment_problems()


@pytest.mark.parametrize("name", REFUSED)
def test_anthropic_with_sdk_environment_is_refused_at_start(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _write(kek_dir, "anthropic_api_key", PLANTED)
    monkeypatch.setenv(name, "planted-header-value")
    with pytest.raises(ModelProviderRefused, match=name) as raised:
        check_worker_model_provider(Settings(model_provider="anthropic"))
    assert "planted-header-value" not in str(raised.value)
    assert PLANTED not in str(raised.value)


@pytest.mark.parametrize("field", NEW_KEYS)
@pytest.mark.parametrize("name", ["OPENAI_BASE_URL", "DEEPSEEK_API_KEY", "ANTHROPIC_LOG"])
def test_fake_with_a_real_providers_key_refuses_the_sdk_environment(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch, field: str, name: str
) -> None:
    """A key file present means a real provider may be built (through a route, G2): the
    environment must be clean although the bootstrap provider is ``fake``."""
    _write(kek_dir, field, PLANTED)
    monkeypatch.setenv(name, "x")
    with pytest.raises(ModelProviderRefused, match=name):
        check_worker_model_provider(Settings(model_provider="fake"))


def test_fake_without_any_key_ignores_the_sdk_environment(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for field in MODEL_KEY_FIELDS.values():
        _write(kek_dir, field, "")  # the empty placeholders count as none
    for name in REFUSED:
        monkeypatch.setenv(name, "x")
    check_worker_model_provider(Settings(model_provider="fake"))


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "DEEPSEEK_BASE_URL"])
def test_the_anthropic_provider_is_not_built_with_sdk_environment(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _write(kek_dir, "anthropic_api_key", PLANTED)
    monkeypatch.setenv(name, "x")
    with pytest.raises(ProviderUnavailable, match=name):
        provider_for(Settings(model_provider="anthropic"))


def test_the_worker_process_refuses_an_openai_variable(kek_dir: Path) -> None:
    _write(kek_dir, "anthropic_api_key", PLANTED)
    result = subprocess.run(
        [sys.executable, "-m", "classbeat.worker"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CLASSBEAT_MODEL_PROVIDER": "anthropic",
            "OPENAI_BASE_URL": "https://planted.example",
        },
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "OPENAI_BASE_URL" in json.loads(result.stdout.splitlines()[-1])["reason"]
    assert "planted.example" not in result.stdout + result.stderr
    assert PLANTED not in result.stdout + result.stderr
