"""Secret files the process cannot read (runbook vps-deploy.md section 5).

Docker Compose bind-mounts a secret file with its host owner and mode, so a host file the
container user cannot read is unreadable inside the container. An optional secret is then treated
as absent with a warning naming the file; a required one still refuses the start. Neither the
warning nor the error carries the file's content.

The unreadable file is simulated by making ``Path.read_bytes`` raise for that one path, so the
tests also hold when the suite runs as root (which reads a mode-000 file anyway).
"""

import logging
from pathlib import Path

import pytest

from classbeat.config import (
    OPTIONAL_SECRET_FILES,
    SECRET_FILE_FIELDS,
    SecretFileUnreadable,
    Settings,
    check_web_has_no_model_key,
    check_worker_has_no_swedebeat_app,
    read_secret_files,
)
from classbeat.crypto import keks

CONTENT = "do-not-show-this-0123456789abcdef0123456789abcdef"
REQUIRED_SECRET_FILES = tuple(n for n in SECRET_FILE_FIELDS if n not in OPTIONAL_SECRET_FILES)


def _unreadable(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    blocked = {p.resolve() for p in paths}
    original = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self.resolve() in blocked:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_the_optional_set_is_pinned() -> None:
    # Adding a name here lets an unreadable file pass silently: only for secrets whose absence
    # the app handles by switching a feature off (or using another source).
    assert {
        "database_maint_password",
        "smtp_password",
        "oauth_google_client_secret",
        "oauth_github_client_secret",
        "oauth_microsoft_client_secret",
        "swedebeat_app",
    } == OPTIONAL_SECRET_FILES
    assert set(SECRET_FILE_FIELDS) >= OPTIONAL_SECRET_FILES
    for required in (
        "database_password",
        "database_owner_password",
        "app_secret_key",
        "anthropic_api_key",
        "openai_api_key",
        "deepseek_api_key",
    ):
        assert required in REQUIRED_SECRET_FILES


@pytest.mark.parametrize("name", sorted(OPTIONAL_SECRET_FILES))
def test_an_unreadable_optional_secret_is_absent_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    (tmp_path / name).write_text(CONTENT, encoding="utf-8")
    (tmp_path / "database_password").write_text("app-pw\n", encoding="utf-8")
    _unreadable(monkeypatch, tmp_path / name)
    with caplog.at_level(logging.WARNING, logger="classbeat.config"):
        values = read_secret_files(tmp_path, (name, "database_password"))
    assert values == {"database_password": "app-pw"}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert name in warnings[0].getMessage()
    assert getattr(warnings[0], "secret_file", None) == name
    assert CONTENT not in caplog.text
    assert str(tmp_path) not in caplog.text


@pytest.mark.parametrize("name", REQUIRED_SECRET_FILES)
def test_an_unreadable_required_secret_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    (tmp_path / name).write_text(CONTENT, encoding="utf-8")
    _unreadable(monkeypatch, tmp_path / name)
    with pytest.raises(SecretFileUnreadable) as raised:
        read_secret_files(tmp_path, (name,))
    message = str(raised.value)
    assert name in message
    assert CONTENT not in message
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_settings_refuse_an_unreadable_required_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "app_secret_key").write_text(CONTENT, encoding="utf-8")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    _unreadable(monkeypatch, tmp_path / "app_secret_key")
    with pytest.raises(SecretFileUnreadable, match="app_secret_key"):
        Settings()


def test_settings_treat_an_unreadable_oauth_secret_as_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "oauth_google_client_secret").write_text(CONTENT, encoding="utf-8")
    (tmp_path / "app_secret_key").write_text("k" * 40, encoding="utf-8")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    _unreadable(monkeypatch, tmp_path / "oauth_google_client_secret")
    settings = Settings()
    assert settings.oauth_google_client_secret is None
    assert settings.app_secret_key is not None


def test_an_unreadable_swedebeat_app_on_the_worker_is_not_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The guard is about the worker holding Swedebeat's key; a file it cannot read is not held.
    (tmp_path / "swedebeat_app").write_text('{"key": "' + "k" * 32 + '", "iv": "' + "i" * 16 + '"}')
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    _unreadable(monkeypatch, tmp_path / "swedebeat_app")
    settings = Settings()
    assert settings.swedebeat_app is None
    check_worker_has_no_swedebeat_app(settings)


def test_an_unreadable_model_key_on_web_still_refuses_the_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Model keys stay required-readable: a model key file mounted on web is refused whether or
    # not web can read it (unchanged from before; check_web_has_no_model_key never runs).
    (tmp_path / "anthropic_api_key").write_text("sk-test-key", encoding="utf-8")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    _unreadable(monkeypatch, tmp_path / "anthropic_api_key")
    with pytest.raises(SecretFileUnreadable, match="anthropic_api_key"):
        check_web_has_no_model_key(Settings())


def test_an_unreadable_connection_kek_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / keks.CURRENT_FILE).write_text("a" * 64, encoding="ascii")
    _unreadable(monkeypatch, tmp_path / keks.CURRENT_FILE)
    with pytest.raises(keks.KekError) as raised:
        keks.read_current("kek-1", tmp_path)
    assert "a" * 64 not in str(raised.value)
