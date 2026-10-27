"""Application settings, read from the environment (prefix ``CLASSBEAT_``) and secret files."""

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

DATABASE_DRIVER = "postgresql+psycopg"
DEFAULT_SECRETS_DIR = "/run/secrets"
# Fields read from secret files, and never from the environment.
SECRET_FILE_FIELDS = ("database_password",)


def secrets_dir() -> Path:
    """Where secret files live: ``CLASSBEAT_SECRETS_DIR``, default ``/run/secrets``."""
    return Path(os.environ.get("CLASSBEAT_SECRETS_DIR") or DEFAULT_SECRETS_DIR)


def read_secret_files(directory: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Read ``<directory>/<name>`` for each name; missing files and directories are skipped."""
    if not directory.is_dir():
        return {}
    values: dict[str, str] = {}
    for name in names:
        path = directory / name
        if path.is_file():
            values[name] = path.read_text(encoding="utf-8").strip()
    return values


class SecretFilesSettingsSource(PydanticBaseSettingsSource):
    """Secrets come only from files, named after the field without the env prefix.

    ``/run/secrets/database_password`` sets ``database_password``. pydantic-settings' own
    secrets source would look for ``classbeat_database_password`` because of ``env_prefix``.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # unused: __call__ reads the files directly

    def __call__(self) -> dict[str, Any]:
        return dict(read_secret_files(secrets_dir(), SECRET_FILE_FIELDS))


class WithoutSecretsSource(PydanticBaseSettingsSource):
    """Wraps the environment source and drops the secret fields: env vars never set secrets."""

    def __init__(self, inner: PydanticBaseSettingsSource) -> None:
        super().__init__(inner.settings_cls)
        self._inner = inner

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._inner.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._inner().items() if k not in SECRET_FILE_FIELDS}


class Settings(BaseSettings):
    """All configuration of the web app and the worker.

    Everything that can carry a credential is a ``SecretStr``: it is masked in ``repr``,
    ``str``, ``model_dump`` and validation errors.
    """

    model_config = SettingsConfigDict(
        env_prefix="CLASSBEAT_",
        env_ignore_empty=True,
        hide_input_in_errors=True,
        extra="ignore",
    )

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # May embed a password, so it is a secret too.
    database_url: SecretStr = SecretStr(f"{DATABASE_DRIVER}://classbeat@127.0.0.1:5432/classbeat")
    # From the file ``database_password`` in the secrets dir (never the environment).
    # When set, replaces the password in ``database_url``.
    database_password: SecretStr | None = None
    static_dir: Path | None = None
    worker_heartbeat_seconds: float = 30.0

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            WithoutSecretsSource(env_settings),
            SecretFilesSettingsSource(settings_cls),
        )

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError("database_url is not a valid database URL") from None
        if url.drivername != DATABASE_DRIVER:
            raise ValueError(f"database_url must use the {DATABASE_DRIVER}:// scheme")
        return value

    @field_validator("worker_heartbeat_seconds")
    @classmethod
    def _check_heartbeat(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("worker_heartbeat_seconds must be greater than 0")
        return value

    def sqlalchemy_url(self) -> URL:
        """The database URL, with ``database_password`` substituted when it is set."""
        url = make_url(self.database_url.get_secret_value())
        if self.database_password is not None:
            url = url.set(password=self.database_password.get_secret_value())
        return url
