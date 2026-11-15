"""The ``swedebeat_gateway_key`` setting (ADR 0035 section 2): a secret file only (the secrets
directory's, or the one ``CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE`` names), never an environment
value; empty is none; a malformed value refuses the start without echoing it; and "available"
is the URL and the key together."""

import logging
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from classbeat.config import SECRET_FILE_FIELDS, Settings
from classbeat.swedebeat_gateway.key_policy import KEY_MAX, KEY_MIN, check_key, gateway_available

KEY = "fake-gateway-client-key-Zq4mR8"
APP_KEY = "k" * 40


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "run-secrets"
    directory.mkdir()
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(directory))
    monkeypatch.delenv("CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE", raising=False)
    monkeypatch.delenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", raising=False)
    return directory


def test_the_key_is_a_secret_file_field() -> None:
    assert "swedebeat_gateway_key" in SECRET_FILE_FIELDS


def test_the_key_comes_from_the_secret_file(secrets: Path) -> None:
    (secrets / "swedebeat_gateway_key").write_text(KEY + "\n", "utf-8")
    key = Settings().swedebeat_gateway_key
    assert key is not None
    assert key.get_secret_value() == KEY
    assert KEY not in repr(Settings())


def test_the_environment_never_sets_the_key(secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_KEY", KEY)
    assert Settings().swedebeat_gateway_key is None
    (secrets / "swedebeat_gateway_key").write_text("file-key-wins-0123456789", "utf-8")
    key = Settings().swedebeat_gateway_key
    assert key is not None
    assert key.get_secret_value() == "file-key-wins-0123456789"


def test_the_file_variable_names_the_key_file_and_replaces_the_directory_one(
    secrets: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (secrets / "swedebeat_gateway_key").write_text("directory-key-0123456789", "utf-8")
    elsewhere = tmp_path / "elsewhere" / "gateway.key"
    elsewhere.parent.mkdir()
    elsewhere.write_text(KEY, "utf-8")
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE", str(elsewhere))
    key = Settings().swedebeat_gateway_key
    assert key is not None
    assert key.get_secret_value() == KEY
    # A named file that is missing is no key: never the directory's file instead.
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE", str(tmp_path / "missing.key"))
    assert Settings().swedebeat_gateway_key is None


def test_an_unreadable_named_file_is_no_key_with_a_warning(
    secrets: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    named = tmp_path / "gateway.key"
    named.write_text(KEY, "utf-8")
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_KEY_FILE", str(named))
    real = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self.name == "gateway.key":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with caplog.at_level(logging.WARNING, logger="classbeat.config"):
        assert Settings().swedebeat_gateway_key is None
    assert "not readable" in caplog.text
    assert KEY not in caplog.text


@pytest.mark.parametrize("blank", ["", "\n", "   ", " \n"])
def test_an_empty_file_is_no_key(secrets: Path, blank: str) -> None:
    (secrets / "swedebeat_gateway_key").write_text(blank, "utf-8")
    assert Settings().swedebeat_gateway_key is None


def test_a_missing_file_is_no_key(secrets: Path) -> None:
    assert Settings().swedebeat_gateway_key is None


@pytest.mark.parametrize(
    "value",
    [
        "a" * (KEY_MIN - 1),
        "a" * (KEY_MAX + 1),
        "fake-gateway key-with-a-space",
        "fake-gateway-key\twith-a-tab",
        "fake-gateway-key-ünicode-0123",
        "fake-gateway-key-\x00-control-01",
        "  fake-gateway-client-key-Zq4mR8",
        "fake-gateway-client-key-Zq4mR8\r\nX-Injected: 1",
    ],
)
def test_a_malformed_key_refuses_the_start_without_echoing_it(secrets: Path, value: str) -> None:
    (secrets / "swedebeat_gateway_key").write_text(value, "utf-8")
    with pytest.raises(ValidationError) as refused:
        Settings()
    text = str(refused.value)
    assert "swedebeat_gateway_key must be 16 to 256 printable ASCII characters" in text
    assert value.strip() not in text


@pytest.mark.parametrize(
    "value", ["a" * KEY_MIN, "Z" * KEY_MAX, KEY, "!#$%&'()*+,-./:;<=>?@[]^_`{|}~0"]
)
def test_a_key_within_the_bounds_is_kept(value: str) -> None:
    kept = check_key(SecretStr(value))
    assert kept is not None
    assert kept.get_secret_value() == value


class _Settings:
    def __init__(self, url: str | None, key: str | None) -> None:
        self.swedebeat_gateway_url = url
        self.swedebeat_gateway_key = None if key is None else SecretStr(key)


@pytest.mark.parametrize(
    ("url", "key", "available"),
    [
        ("http://192.168.1.222:7355", KEY, True),
        ("http://192.168.1.222:7355", None, False),
        ("http://192.168.1.222:7355", "", False),
        (None, KEY, False),
        ("", KEY, False),
        (None, None, False),
    ],
)
def test_available_needs_the_url_and_the_key(
    url: str | None, key: str | None, available: bool
) -> None:
    assert gateway_available(_Settings(url, key)) is available


def test_available_from_real_settings(secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert gateway_available(Settings()) is False
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", "http://192.168.1.222:7355")
    assert gateway_available(Settings()) is False  # no key yet
    (secrets / "swedebeat_gateway_key").write_text("", "utf-8")
    assert gateway_available(Settings()) is False  # the empty placeholder
    (secrets / "swedebeat_gateway_key").write_text(KEY, "utf-8")
    assert gateway_available(Settings()) is True
    monkeypatch.delenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL")
    assert gateway_available(Settings()) is False
