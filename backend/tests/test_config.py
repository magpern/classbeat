import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from classbeat.config import Settings, read_secret_files

URL = "postgresql+psycopg://app:url-password@db.example:5433/classbeat"


def test_defaults() -> None:
    settings = Settings()
    assert settings.environment == "dev"
    assert settings.log_level == "INFO"
    assert settings.database_password is None
    assert settings.static_dir is None
    assert settings.worker_heartbeat_seconds == 30
    assert settings.sqlalchemy_url().drivername == "postgresql+psycopg"


def test_reads_environment_with_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLASSBEAT_ENVIRONMENT", "prod")
    monkeypatch.setenv("CLASSBEAT_LOG_LEVEL", "debug")
    monkeypatch.setenv("CLASSBEAT_DATABASE_URL", URL)
    monkeypatch.setenv("CLASSBEAT_STATIC_DIR", str(tmp_path))
    monkeypatch.setenv("CLASSBEAT_WORKER_HEARTBEAT_SECONDS", "0.5")
    monkeypatch.setenv("ENVIRONMENT", "dev")  # unprefixed names are ignored
    settings = Settings()
    assert settings.environment == "prod"
    assert settings.log_level == "DEBUG"
    assert settings.database_url.get_secret_value() == URL
    assert settings.static_dir == tmp_path
    assert settings.worker_heartbeat_seconds == 0.5


def test_empty_environment_value_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_STATIC_DIR", "")
    assert Settings().static_dir is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("environment", "staging"),
        ("log_level", "LOUD"),
        ("worker_heartbeat_seconds", 0),
        ("worker_heartbeat_seconds", -1),
    ],
)
def test_rejects_invalid_values(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(**{name: value})  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:leaky-secret@h/db",
        "mysql+pymysql://u:leaky-secret@h/db",
        "not a url, only leaky-secret",
    ],
)
def test_rejects_other_database_urls_without_echoing_them(url: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(database_url=url)  # pyright: ignore[reportArgumentType]
    assert "database_url" in str(excinfo.value)
    assert "leaky-secret" not in str(excinfo.value)


def test_password_from_secret_file_replaces_the_one_in_the_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "database_password").write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    url = Settings(database_url=URL).sqlalchemy_url()  # pyright: ignore[reportArgumentType]
    assert url.password == "from-the-file"
    assert (url.username, url.host, url.port, url.database) == (
        "app",
        "db.example",
        5433,
        "classbeat",
    )
    assert url.drivername == "postgresql+psycopg"


def test_database_password_is_not_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))  # empty: no secret files
    monkeypatch.setenv("CLASSBEAT_DATABASE_URL", URL)
    monkeypatch.setenv("CLASSBEAT_DATABASE_PASSWORD", "from-the-env")
    settings = Settings()
    assert settings.database_password is None
    assert settings.sqlalchemy_url().password == "url-password"


def test_the_secret_file_name_has_no_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "classbeat_database_password").write_text("prefixed", encoding="utf-8")
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path))
    assert Settings().database_password is None


def test_url_password_is_kept_without_database_password() -> None:
    settings = Settings(database_url=URL)  # pyright: ignore[reportArgumentType]
    assert settings.sqlalchemy_url().password == "url-password"


def test_special_characters_in_the_password_survive_rendering() -> None:
    password = "p@ss:w/rd#1 %"
    settings = Settings(
        database_url=URL,  # pyright: ignore[reportArgumentType]
        database_password=password,  # pyright: ignore[reportArgumentType]
    )
    rendered = settings.sqlalchemy_url().render_as_string(hide_password=False)
    assert make_url(rendered).password == password


def test_secrets_do_not_appear_in_repr_str_or_dumps() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://app:url-secret@db.example/classbeat",  # pyright: ignore[reportArgumentType]
        database_password="file-secret",  # pyright: ignore[reportArgumentType]
    )
    shown = [
        repr(settings),
        str(settings),
        str(settings.model_dump()),
        settings.model_dump_json(),
        json.dumps(settings.model_dump(mode="json")),
        repr(settings.sqlalchemy_url()),
        str(settings.sqlalchemy_url()),
    ]
    for text in shown:
        assert "url-secret" not in text
        assert "file-secret" not in text


def test_read_secret_files_ignores_missing_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "present").write_text("  value \n", encoding="utf-8")
    assert read_secret_files(tmp_path, ("present", "absent")) == {"present": "  value "}
    assert read_secret_files(tmp_path / "no-such-dir", ("present",)) == {}


def test_missing_secrets_dir_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLASSBEAT_SECRETS_DIR", str(tmp_path / "missing"))
    assert Settings().database_password is None


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_secret_file_keeps_spaces_and_drops_only_the_trailing_newline(
    tmp_path: Path, ending: str
) -> None:
    (tmp_path / "database_password").write_bytes(f"  pa ss  {ending}".encode())
    assert read_secret_files(tmp_path, ("database_password",)) == {"database_password": "  pa ss  "}
