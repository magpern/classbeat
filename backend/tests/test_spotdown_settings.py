"""The spotdown settings (ADR 0040 section 10): ``CLASSBEAT_SPOTDOWN_URL`` and
``CLASSBEAT_SPOTDOWN_SOURCES`` (off and empty by default), and the ``spotdown_service_key`` secret
file (never the environment, never in a repr or a log line), with "available"."""

import logging
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from classbeat.config import Settings
from classbeat.log import redact
from classbeat.spotdown.key_policy import check_key, spotdown_available
from classbeat.spotdown.sources_policy import SpotdownSourcesRefused, canonical, parse_sources

KEY = "spotdown-settings-test-key-Vb6Nq2Lp8Zx4Rt"


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    for name in ("CLASSBEAT_SPOTDOWN_URL", "CLASSBEAT_SPOTDOWN_SOURCES"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_spotdown_is_off_by_default(secrets: Path) -> None:
    settings = Settings()
    assert settings.spotdown_url is None
    assert settings.spotdown_sources == ""
    assert settings.spotdown_service_key is None
    assert not spotdown_available(settings)


def test_an_empty_url_is_off(secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_URL", "")
    assert Settings().spotdown_url is None


def test_the_url_and_the_key_file_make_spotdown_available(
    secrets: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_URL", "http://spotdown-api:8000/")
    monkeypatch.setenv("CLASSBEAT_SPOTDOWN_SOURCES", "youtube, upload")
    (secrets / "spotdown_service_key").write_text(KEY + "\n", "utf-8")
    settings = Settings()
    assert settings.spotdown_url == "http://spotdown-api:8000"
    assert settings.spotdown_sources == "upload,youtube"
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


@pytest.mark.parametrize(
    ("value", "sources"),
    [
        ("", frozenset[str]()),
        ("  ", frozenset[str]()),
        ("upload", frozenset({"upload"})),
        ("youtube,upload", frozenset({"upload", "youtube"})),
    ],
)
def test_the_sources_are_a_comma_list(value: str, sources: frozenset[str]) -> None:
    assert parse_sources(value) == sources
    assert canonical(value) == ",".join(sorted(sources))


@pytest.mark.parametrize("bad", ["url", "upload,url", "upload,upload", "YouTube", "upload,", "all"])
def test_any_other_source_is_refused(bad: str) -> None:
    with pytest.raises(SpotdownSourcesRefused):
        parse_sources(bad)


def test_the_log_redaction_hides_the_key_by_name() -> None:
    line = redact(f"spotdown_service_key={KEY} Authorization: Bearer {KEY}")
    assert KEY not in line
