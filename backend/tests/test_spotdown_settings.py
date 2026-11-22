"""The spotdown settings (ADR 0040 section 10): ``CLASSBEAT_SPOTDOWN_URL`` (off by default) and the
``spotdown_service_key`` secret file (never the environment, never in a repr or a log line), with
"available". The sources and shadow settings are gone (#1355)."""

import logging
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from classbeat.config import Settings
from classbeat.log import redact
from classbeat.spotdown.key_policy import check_key, spotdown_available

KEY = "spotdown-settings-test-key-Vb6Nq2Lp8Zx4Rt"


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    monkeypatch.delenv("CLASSBEAT_SPOTDOWN_URL", raising=False)
    return tmp_path


def test_spotdown_is_off_by_default(secrets: Path) -> None:
    settings = Settings()
    assert settings.spotdown_url is None
    assert settings.spotdown_service_key is None
    assert not spotdown_available(settings)


def test_an_empty_url_is_off(secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_URL", "")
    assert Settings().spotdown_url is None


def test_the_url_and_the_key_file_make_spotdown_available(
    secrets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_URL", "http://spotdown-api:8000/")
    (secrets / "spotdown_service_key").write_text(KEY + "\n", "utf-8")
    settings = Settings()
    assert settings.spotdown_url == "http://spotdown-api:8000"
    assert settings.spotdown_service_key is not None
    assert settings.spotdown_service_key.get_secret_value() == KEY
    assert spotdown_available(settings)
    assert KEY not in repr(settings) + str(settings.model_dump())


def test_a_url_alone_is_not_available(secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_URL", "http://spotdown-api:8000")
    (secrets / "spotdown_service_key").write_text("\n", "utf-8")  # the empty placeholder
    settings = Settings()
    assert settings.spotdown_service_key is None
    assert not spotdown_available(settings)


def test_the_key_never_comes_from_the_environment(
    secrets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SERVICE_KEY", KEY)
    assert Settings().spotdown_service_key is None


@pytest.mark.parametrize("value", ["https://spotdown.example.com", "http://spotdown-api"])
def test_a_bad_url_refuses_startup_without_the_value(
    secrets: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_URL", value)
    with pytest.raises(ValidationError) as refused:
        Settings()
    assert value not in str(refused.value)


@pytest.mark.parametrize("bad", ["short", "x" * 31, "x" * 257, "a" * 20 + " " + "b" * 20, "é" * 40])
def test_a_bad_key_file_refuses_startup_without_the_value(
    secrets: Path, bad: str, caplog: pytest.LogCaptureFixture
) -> None:
    (secrets / "spotdown_service_key").write_text(bad, "utf-8")
    with caplog.at_level(logging.DEBUG), pytest.raises(ValidationError) as refused:
        Settings()
    assert "spotdown_service_key must be 32 to 256" in str(refused.value)
    assert bad not in str(refused.value) + caplog.text


def test_check_key_takes_the_empty_placeholder_as_none() -> None:
    assert check_key(None) is None
    assert check_key(SecretStr("   ")) is None
    key = SecretStr("k" * 32)
    assert check_key(key) is key


def test_the_sources_and_shadow_settings_are_gone(
    secrets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """classbeat uses ``upload`` and ``youtube`` without a setting; a set variable is never read,
    so even a value the old list refused does not stop the start."""
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SOURCES", "url")
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SHADOW", "true")
    settings = Settings()
    assert not hasattr(settings, "spotdown_sources")
    assert not hasattr(settings, "spotdown_shadow")


def test_the_log_redaction_hides_the_key_by_name() -> None:
    line = redact(f"spotdown_service_key={KEY} Authorization: Bearer {KEY}")
    assert KEY not in line
