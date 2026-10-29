"""The model key and ``CLASSBEAT_MODEL_PROVIDER`` (ADR 0006 §9): the key comes only from the
``anthropic_api_key`` secret file, the worker refuses to start without it for ``anthropic`` and
with ``fake`` in prod, and the web app refuses to start when it can read the key."""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from classbeat import egress
from classbeat.config import (
    ModelProviderRefused,
    Settings,
    check_web_has_no_model_key,
    check_worker_model_provider,
)
from classbeat.web import create_app
from classbeat.worker import __main__ as worker_main

# A planted value shaped like a real key; it is never sent anywhere.
PLANTED_KEY = "sk-ant-api03-planted-test-value-0123456789abcdef"


def _field(obj: object, name: str) -> Any:
    """A stdlib attribute the type stubs leave out."""
    return getattr(obj, name)


def _key_file(directory: Path, value: str | bytes) -> None:
    data = value.encode() if isinstance(value, str) else value
    (directory / "anthropic_api_key").write_bytes(data)


def test_the_default_provider_is_anthropic() -> None:
    assert Settings().model_provider == "anthropic"


def test_the_key_comes_from_its_secret_file(kek_dir: Path) -> None:
    _key_file(kek_dir, PLANTED_KEY + "\n")
    key = Settings().anthropic_api_key
    assert key is not None
    assert key.get_secret_value() == PLANTED_KEY


def test_the_key_is_never_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_ANTHROPIC_API_KEY", PLANTED_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", PLANTED_KEY)
    assert Settings().anthropic_api_key is None


@pytest.mark.parametrize("content", ["", "\n", "   ", " \r\n"])
def test_an_empty_placeholder_file_is_no_key(kek_dir: Path, content: str) -> None:
    _key_file(kek_dir, content)
    assert Settings().anthropic_api_key is None


@pytest.mark.parametrize("bad", ["sk-ant two", "sk-ant\ttab", "sk-ant\x00nul", "sk-ant-é"])
def test_a_key_with_whitespace_or_control_characters_is_refused_without_echo(
    kek_dir: Path, bad: str
) -> None:
    _key_file(kek_dir, bad)
    with pytest.raises(ValueError, match="anthropic_api_key") as raised:
        Settings()
    assert bad not in str(raised.value)


def test_the_key_is_masked(kek_dir: Path) -> None:
    _key_file(kek_dir, PLANTED_KEY)
    settings = Settings()
    for text in (repr(settings), str(settings), str(settings.model_dump())):
        assert PLANTED_KEY not in text
    assert PLANTED_KEY not in settings.model_dump_json()


def test_anthropic_without_the_key_file_is_refused() -> None:
    with pytest.raises(ModelProviderRefused, match="anthropic_api_key"):
        check_worker_model_provider(Settings(model_provider="anthropic"))


def test_anthropic_with_an_empty_key_file_is_refused(kek_dir: Path) -> None:
    _key_file(kek_dir, "")
    with pytest.raises(ModelProviderRefused):
        check_worker_model_provider(Settings(model_provider="anthropic"))


@pytest.mark.parametrize("environment", ["dev", "test", "prod"])
def test_anthropic_with_the_key_starts(kek_dir: Path, environment: str) -> None:
    _key_file(kek_dir, PLANTED_KEY)
    settings = Settings(
        model_provider="anthropic",
        environment=environment,  # pyright: ignore[reportArgumentType]
        public_base_url="https://classbeat.example",
    )
    check_worker_model_provider(settings)


@pytest.mark.parametrize("name", ["ANTHROPIC_LOG", "ANTHROPIC_CUSTOM_HEADERS", "SSLKEYLOGFILE"])
def test_anthropic_with_sdk_environment_is_refused_at_start(
    kek_dir: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _key_file(kek_dir, PLANTED_KEY)
    monkeypatch.setenv(name, "x-api-key: planted-header-value")
    with pytest.raises(ModelProviderRefused, match=name) as raised:
        check_worker_model_provider(Settings(model_provider="anthropic"))
    assert "planted-header-value" not in str(raised.value)
    assert PLANTED_KEY not in str(raised.value)


@pytest.mark.parametrize("environment", ["dev", "test"])
def test_fake_ignores_the_model_sdk_environment(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    check_worker_model_provider(
        Settings(model_provider="fake", environment=environment)  # pyright: ignore[reportArgumentType]
    )


def test_the_worker_process_refuses_anthropic_with_sdk_logging(kek_dir: Path) -> None:
    _key_file(kek_dir, PLANTED_KEY)
    result = _run_worker({"CLASSBEAT_MODEL_PROVIDER": "anthropic", "ANTHROPIC_LOG": "debug"})
    assert result.returncode == 2
    assert "ANTHROPIC_LOG" in json.loads(result.stdout.splitlines()[-1])["reason"]
    assert PLANTED_KEY not in result.stdout + result.stderr


def test_the_worker_process_refuses_anthropic_with_a_tls_key_log(
    kek_dir: Path, tmp_path: Path
) -> None:
    _key_file(kek_dir, PLANTED_KEY)
    key_log = tmp_path / "keys.log"
    result = _run_worker({"CLASSBEAT_MODEL_PROVIDER": "anthropic", "SSLKEYLOGFILE": str(key_log)})
    assert result.returncode == 2
    assert "SSLKEYLOGFILE" in json.loads(result.stdout.splitlines()[-1])["reason"]
    assert str(key_log) not in result.stdout + result.stderr
    assert not key_log.exists()  # the allowlist's TLS context, built first, never opened it


@pytest.mark.parametrize("environment", ["dev", "test"])
def test_fake_starts_outside_prod(environment: str) -> None:
    check_worker_model_provider(
        Settings(model_provider="fake", environment=environment)  # pyright: ignore[reportArgumentType]
    )


def test_fake_in_prod_is_refused(kek_dir: Path) -> None:
    _key_file(kek_dir, PLANTED_KEY)  # a key does not make the fake acceptable
    settings = Settings(
        model_provider="fake", environment="prod", public_base_url="https://classbeat.example"
    )
    with pytest.raises(ModelProviderRefused, match="prod"):
        check_worker_model_provider(settings)


def test_the_provider_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_MODEL_PROVIDER", "fake")
    assert Settings().model_provider == "fake"
    monkeypatch.setenv("CLASSBEAT_MODEL_PROVIDER", "openai")
    with pytest.raises(ValueError, match="model_provider"):
        Settings()


def test_the_web_app_refuses_to_start_when_it_can_read_the_key(kek_dir: Path) -> None:
    _key_file(kek_dir, PLANTED_KEY)
    with pytest.raises(ModelProviderRefused, match="web") as raised:
        create_app(Settings())
    assert PLANTED_KEY not in str(raised.value)


def test_the_web_app_starts_with_the_empty_placeholder(kek_dir: Path) -> None:
    _key_file(kek_dir, "")
    check_web_has_no_model_key(Settings())
    create_app(Settings())


@pytest.mark.usefixtures("restore_logging")
def test_the_worker_installs_the_allowlist_before_reading_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(urllib.request, "_opener", None)
    seen: list[object] = []

    class Stop(Exception):
        pass

    def settings_probe() -> Settings:
        seen.append(_field(urllib.request, "_opener"))
        raise Stop

    monkeypatch.setattr(worker_main, "Settings", settings_probe)
    with pytest.raises(Stop):
        worker_main.main()
    [opener] = seen
    assert isinstance(opener, urllib.request.OpenerDirector)
    with pytest.raises(egress.OutboundBlocked):
        opener.open("https://evil.example/", timeout=1)


@pytest.mark.usefixtures("restore_logging")
def test_the_worker_main_refuses_before_any_database_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(urllib.request, "_opener", None)

    async def must_not_run(_: Settings) -> None:
        raise AssertionError("the worker went on after a refused provider")

    monkeypatch.setattr(worker_main, "_run", must_not_run)
    monkeypatch.setenv("CLASSBEAT_MODEL_PROVIDER", "anthropic")
    assert worker_main.main() == 2


def _run_worker(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "classbeat.worker"],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=30,
        check=False,
    )


def test_the_worker_process_refuses_anthropic_without_a_key(kek_dir: Path) -> None:
    _key_file(kek_dir, "")
    result = _run_worker({"CLASSBEAT_MODEL_PROVIDER": "anthropic"})
    assert result.returncode == 2
    entry = json.loads(result.stdout.splitlines()[-1])
    assert entry["message"] == "worker refused to start"
    assert "anthropic_api_key" in entry["reason"]


def test_the_worker_process_refuses_fake_in_prod(kek_dir: Path) -> None:
    _key_file(kek_dir, PLANTED_KEY)
    result = _run_worker(
        {
            "CLASSBEAT_MODEL_PROVIDER": "fake",
            "CLASSBEAT_ENVIRONMENT": "prod",
            "CLASSBEAT_PUBLIC_BASE_URL": "https://classbeat.example",
        }
    )
    assert result.returncode == 2
    assert "prod" in json.loads(result.stdout.splitlines()[-1])["reason"]
    assert PLANTED_KEY not in result.stdout + result.stderr
