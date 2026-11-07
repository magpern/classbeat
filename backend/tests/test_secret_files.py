"""Unreadable and blank secret files (#511).

In production every secret file is ``<deploy user>:10010`` mode 0640 and the containers read it
through the supplementary group 10010; a file left at 0600 cannot be read. An optional secret
(SMTP, the OAuth client secrets, the Swedebeat override) then counts as not set and one warning
names the field; a required one stays fatal. A file holding only whitespace is the empty
placeholder (that feature off), except the database passwords, which the SQL init scripts read
byte for byte the same way.
"""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from classbeat.auth.oauth_providers import enabled_providers
from classbeat.config import (
    BYTE_EXACT_SECRET_FIELDS,
    OPTIONAL_SECRET_FILE_FIELDS,
    SECRET_FILE_FIELDS,
    Settings,
    read_secret_files,
)

SECRET = "s3cr3t-value-that-must-never-be-logged"
APP_KEY = "k" * 40
REQUIRED = sorted(set(SECRET_FILE_FIELDS) - OPTIONAL_SECRET_FILE_FIELDS)


def unreadable(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """``read_bytes`` of these files raises as for a 0600 file of another owner (tests may run as
    root, where a real chmod would not stop the read)."""
    real = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self.name in names:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    (tmp_path / "app_secret_key").write_text(APP_KEY, "utf-8")
    return tmp_path


def test_the_optional_secrets_are_exactly_these() -> None:
    assert sorted(OPTIONAL_SECRET_FILE_FIELDS) == [
        "oauth_github_client_secret",
        "oauth_google_client_secret",
        "oauth_microsoft_client_secret",
        "smtp_password",
        "swedebeat_app",
        "turnstile_secret_key",
    ]
    assert set(SECRET_FILE_FIELDS) >= OPTIONAL_SECRET_FILE_FIELDS
    assert not OPTIONAL_SECRET_FILE_FIELDS & BYTE_EXACT_SECRET_FIELDS


@pytest.mark.parametrize("name", sorted(OPTIONAL_SECRET_FILE_FIELDS))
def test_an_unreadable_optional_secret_is_not_set_and_logs_only_its_name(
    monkeypatch: pytest.MonkeyPatch,
    secrets: Path,
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    (secrets / name).write_text(SECRET, "utf-8")
    unreadable(monkeypatch, name)
    with caplog.at_level(logging.WARNING, logger="classbeat.config"):
        settings = Settings()
    assert getattr(settings, name) is None
    assert settings.app_secret_key is not None  # the other files are still read
    records = [r for r in caplog.records if r.name == "classbeat.config"]
    assert [r.getMessage() for r in records] == [
        f"secret file {name} is not readable; treated as not set"
    ]
    assert SECRET not in caplog.text
    assert str(secrets) not in caplog.text


def test_an_unreadable_oauth_secret_leaves_that_provider_off(
    monkeypatch: pytest.MonkeyPatch, secrets: Path
) -> None:
    monkeypatch.setenv("CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID", "id-google")
    monkeypatch.setenv("CLASSBEAT_OAUTH_GITHUB_CLIENT_ID", "id-github")
    (secrets / "oauth_google_client_secret").write_text(SECRET, "utf-8")
    (secrets / "oauth_github_client_secret").write_text(SECRET, "utf-8")
    unreadable(monkeypatch, "oauth_google_client_secret")
    assert set(enabled_providers(Settings())) == {"github"}


@pytest.mark.parametrize("name", REQUIRED)
def test_an_unreadable_required_secret_is_fatal(
    monkeypatch: pytest.MonkeyPatch, secrets: Path, name: str
) -> None:
    (secrets / name).write_text(SECRET, "utf-8")
    unreadable(monkeypatch, name)
    with pytest.raises(PermissionError):
        Settings()


def test_read_secret_files_raises_for_an_unreadable_file_not_named_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "a").write_text("x", "utf-8")
    (tmp_path / "b").write_text("y", "utf-8")
    unreadable(monkeypatch, "a")
    assert read_secret_files(tmp_path, ("a", "b"), frozenset({"a"})) == {"b": "y"}
    with pytest.raises(PermissionError):
        read_secret_files(tmp_path, ("a", "b"))


@pytest.mark.parametrize("blank", [" ", "   \n", "\t", " \r\n", "\n\n", chr(0xA0)])
def test_a_blank_oauth_secret_is_empty_and_the_provider_off(
    monkeypatch: pytest.MonkeyPatch, secrets: Path, blank: str
) -> None:
    monkeypatch.setenv("CLASSBEAT_OAUTH_GOOGLE_CLIENT_ID", "id-google")
    (secrets / "oauth_google_client_secret").write_text(blank, "utf-8")
    assert read_secret_files(secrets, ("oauth_google_client_secret",)) == {
        "oauth_google_client_secret": ""
    }
    assert enabled_providers(Settings()) == {}


def test_a_blank_smtp_password_is_empty(secrets: Path) -> None:
    (secrets / "smtp_password").write_text("  \n", "utf-8")
    password = Settings().smtp_password
    assert password is None or password.get_secret_value() == ""


def test_a_blank_app_secret_key_is_refused(secrets: Path) -> None:
    (secrets / "app_secret_key").write_text(" " * 64, "utf-8")
    with pytest.raises(ValidationError, match="app_secret_key"):
        Settings()


@pytest.mark.parametrize("name", sorted(BYTE_EXACT_SECRET_FIELDS))
def test_database_passwords_stay_byte_exact(tmp_path: Path, name: str) -> None:
    # The same bytes as 10-roles.sql / 31-maint-password.psql read: one LF, then one CR, no more.
    (tmp_path / name).write_text("   \n", "utf-8")
    assert read_secret_files(tmp_path, (name,)) == {name: "   "}


def test_spaces_inside_a_secret_are_kept(tmp_path: Path) -> None:
    (tmp_path / "oauth_google_client_secret").write_text("  a b  \n", "utf-8")
    assert read_secret_files(tmp_path, ("oauth_google_client_secret",)) == {
        "oauth_google_client_secret": "  a b  "
    }
