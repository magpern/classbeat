"""Application settings, read from the environment (prefix ``CLASSBEAT_``) and secret files."""

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from classbeat.emailnorm import normalize_email

DATABASE_DRIVER = "postgresql+psycopg"
DEFAULT_SECRETS_DIR = "/run/secrets"
# Fields read from secret files, and never from the environment.
SECRET_FILE_FIELDS = (
    "database_password",
    "database_owner_password",
    "app_secret_key",
    "smtp_password",
    "oauth_google_client_secret",
    "oauth_github_client_secret",
    "oauth_microsoft_client_secret",
    "anthropic_api_key",
)
# Hosts where unencrypted SMTP (``smtp_tls = none``) is allowed: loopback and the compose service.
PLAIN_SMTP_HOSTS = frozenset({"localhost", "mailpit"})
MIN_APP_SECRET_BYTES = 32
# ADR 0004 section 2: the id of the current KEK (the ``connection_kek`` secret file).
CONNECTION_KEK_ID = re.compile(r"kek-[a-z0-9-]{1,28}")


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
            text = path.read_bytes().decode("utf-8")
            # Only the trailing newline an editor or `echo` adds; spaces may be part of a secret.
            values[name] = text.removesuffix("\n").removesuffix("\r")
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
    # The app role (``classbeat_app``): web and worker. ADR 0002 section 4.
    database_url: SecretStr = SecretStr(
        f"{DATABASE_DRIVER}://classbeat_app@127.0.0.1:5432/classbeat"
    )
    # From the file ``database_password`` in the secrets dir (never the environment).
    # When set, replaces the password in ``database_url``.
    database_password: SecretStr | None = None
    # The owner role (``classbeat_owner``): Alembic only, never web or worker.
    migrations_database_url: SecretStr = SecretStr(
        f"{DATABASE_DRIVER}://classbeat_owner@127.0.0.1:5432/classbeat"
    )
    # From the file ``database_owner_password``; replaces the password in the URL above.
    database_owner_password: SecretStr | None = None
    static_dir: Path | None = None
    # The worker's claim poll when no ``jobs_queued`` notification arrives (ADR 0005 section 3).
    worker_poll_seconds: float = 2.0
    # How often the worker purges expired auth rows (ADR 0003 section 1: hourly).
    purge_interval_seconds: float = 3600.0

    # Accounts and sessions (ADR 0003).
    # From the file ``app_secret_key`` (at least 32 bytes); the web app refuses to start without
    # it. All auth keys are derived from it (section 8).
    app_secret_key: SecretStr | None = None
    # The one public origin, for the Origin check (section 4) and for links in mail.
    public_base_url: str = "http://127.0.0.1:8000"
    # Comma-separated addresses that become admin at their next sign-in (section 7).
    admin_emails: str = ""
    # Comma-separated addresses or CIDR ranges of reverse proxies whose X-Forwarded-For is
    # believed (section 9). Empty: the peer address is the client.
    trusted_proxies: str = ""
    session_idle_days: int = 7
    session_absolute_days: int = 30
    password_hash_workers: int = 4
    # The id of the current key-encryption key (ADR 0004 section 2). The key itself is the
    # secret file ``connection_kek`` (read by ``classbeat.crypto.keks``, never the environment);
    # retired keys are ``connection_kek_<id>``.
    connection_kek_id: str = "kek-1"
    # Who may create an account (section 5): ``invite`` (default), ``closed`` or ``open``.
    signup_mode: Literal["invite", "closed", "open"] = "invite"

    # Mail (section 5). Unset ``smtp_host``: no mail is sent (a warning is logged instead).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_sender: str = "classbeat@localhost"
    smtp_tls: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str | None = None
    # From the file ``smtp_password`` (never the environment).
    smtp_password: SecretStr | None = None

    # OAuth sign-in (section 5). A provider is on only when its client id and its secret file
    # (``oauth_<provider>_client_secret``, never the environment) both exist.
    oauth_google_client_id: str | None = None
    oauth_github_client_id: str | None = None
    oauth_microsoft_client_id: str | None = None
    oauth_google_client_secret: SecretStr | None = None
    oauth_github_client_secret: SecretStr | None = None
    oauth_microsoft_client_secret: SecretStr | None = None

    # The model provider of the worker (ADR 0006 §9): ``anthropic`` (the default) needs the
    # ``anthropic_api_key`` secret file; ``fake`` (the dev stack and CI) is refused in ``prod``.
    # ``check_worker_model_provider`` enforces both at worker start.
    model_provider: Literal["anthropic", "fake"] = "anthropic"

    # Jobs (ADR 0005 sections 3 and 4).
    job_lease_seconds: int = Field(default=120, ge=30, le=600)
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    # Reap, expire and settle (ADR 0005 section 3: every 30 s).
    worker_maintenance_seconds: float = Field(default=30.0, gt=0)
    # On SIGTERM, how long runs may take to reach a checkpoint or return from a call before
    # their leases are handed back (inside compose's 30 s stop_grace_period).
    worker_shutdown_wait_seconds: float = Field(default=25.0, ge=0, le=25)
    approval_ttl_hours: int = Field(default=168, ge=1, le=8760)
    # operation_busy requeues per phase that cost no attempt; after that each one counts (#86).
    job_busy_requeue_limit: int = Field(default=5, ge=0, le=100)
    # From the file ``anthropic_api_key`` (never the environment), mounted on the worker only;
    # the web app refuses to start when it can read it. An empty file counts as no key
    # (``scripts/worktree.sh init`` creates an empty one; it is never a real key).
    anthropic_api_key: SecretStr | None = None

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

    @field_validator("database_url", "migrations_database_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError(f"{info.field_name} is not a valid database URL") from None
        if url.drivername != DATABASE_DRIVER:
            raise ValueError(f"{info.field_name} must use the {DATABASE_DRIVER}:// scheme")
        return value

    @field_validator("worker_poll_seconds")
    @classmethod
    def _check_poll(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("worker_poll_seconds must be greater than 0")
        return value

    @field_validator("app_secret_key")
    @classmethod
    def _check_app_secret_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode()) < MIN_APP_SECRET_BYTES:
            raise ValueError(f"app_secret_key must be at least {MIN_APP_SECRET_BYTES} bytes long")
        return value

    @field_validator("anthropic_api_key")
    @classmethod
    def _check_anthropic_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        key = value.get_secret_value()
        if not key.strip():
            return None  # the empty placeholder: no key
        # It goes into an HTTP header: no whitespace or control characters anywhere.
        if not key.isascii() or any(c.isspace() or not c.isprintable() for c in key):
            raise ValueError("anthropic_api_key has whitespace, control or non-ASCII characters")
        return value

    @field_validator("connection_kek_id")
    @classmethod
    def _check_connection_kek_id(cls, value: str) -> str:
        if CONNECTION_KEK_ID.fullmatch(value) is None:
            raise ValueError("connection_kek_id must match ^kek-[a-z0-9-]{1,28}$")
        return value

    @field_validator("public_base_url")
    @classmethod
    def _check_public_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
            or parts.username
            or parts.password
        ):
            raise ValueError("public_base_url must be an http(s) origin without a path")
        return public_origin(value)

    @model_validator(mode="after")
    def _plain_smtp_only_locally(self) -> "Settings":
        if (
            self.smtp_tls == "none"
            and self.smtp_host is not None
            and not _is_local_host(self.smtp_host)
        ):
            raise ValueError(
                "smtp_tls=none is only allowed for a loopback host or the mailpit service"
            )
        return self

    @field_validator("smtp_port")
    @classmethod
    def _check_smtp_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("smtp_port must be between 1 and 65535")
        return value

    @model_validator(mode="after")
    def _prod_needs_https(self) -> "Settings":
        if self.environment == "prod" and not self.public_base_url.startswith("https://"):
            raise ValueError("public_base_url must be https:// when environment is prod")
        return self

    @field_validator("trusted_proxies")
    @classmethod
    def _check_trusted_proxies(cls, value: str) -> str:
        parse_networks(value)
        return value

    @field_validator("session_idle_days", "session_absolute_days", "password_hash_workers")
    @classmethod
    def _check_positive(cls, value: int, info: ValidationInfo) -> int:
        if value < 1:
            raise ValueError(f"{info.field_name} must be at least 1")
        return value

    @field_validator("purge_interval_seconds")
    @classmethod
    def _check_purge_interval(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("purge_interval_seconds must be greater than 0")
        return value

    @property
    def admin_email_set(self) -> frozenset[str]:
        """``admin_emails`` in the normalized form of ADR 0003 section 1 (ASCII lower case)."""
        return frozenset(normalize_email(e) for e in self.admin_emails.split(",") if e.strip())

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return parse_networks(self.trusted_proxies)

    def sqlalchemy_url(self) -> URL:
        """The app role's URL, with ``database_password`` substituted when it is set."""
        return _with_password(self.database_url, self.database_password)

    def migrations_sqlalchemy_url(self) -> URL:
        """The owner role's URL, with ``database_owner_password`` substituted when it is set."""
        return _with_password(self.migrations_database_url, self.database_owner_password)


def _is_local_host(host: str) -> bool:
    if host.lower() in PLAIN_SMTP_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def public_origin(url: str) -> str:
    """``scheme://host[:port]`` of ``url``, lower case, as a browser sends it in ``Origin``."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    port = f":{parts.port}" if parts.port is not None and parts.port != default else ""
    return f"{parts.scheme.lower()}://{host.lower()}{port}"


def parse_networks(
    value: str,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse a comma-separated list of addresses and CIDR ranges."""
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in value.split(","):
        if item.strip():
            try:
                networks.append(ipaddress.ip_network(item.strip(), strict=False))
            except ValueError:
                raise ValueError("trusted_proxies must list IP addresses or CIDR ranges") from None
    return tuple(networks)


def _with_password(url: SecretStr, password: SecretStr | None) -> URL:
    result = make_url(url.get_secret_value())
    if password is not None:
        result = result.set(password=password.get_secret_value())
    return result


class ModelProviderRefused(Exception):
    """The worker's model-provider settings are not allowed; the message names the rule only."""


def check_worker_model_provider(settings: Settings) -> None:
    """ADR 0006 §9: ``anthropic`` needs the ``anthropic_api_key`` file; ``fake`` never in prod."""
    if settings.model_provider == "anthropic" and settings.anthropic_api_key is None:
        raise ModelProviderRefused(
            "model_provider is anthropic but the anthropic_api_key secret file is missing or empty"
        )
    if settings.model_provider == "fake" and settings.environment == "prod":
        raise ModelProviderRefused("model_provider fake is not allowed when environment is prod")


def check_web_has_no_model_key(settings: Settings) -> None:
    """The web app never calls a model, so it never holds the key (ADR 0006 §9)."""
    if settings.anthropic_api_key is not None:
        raise ModelProviderRefused(
            "the web app must not see the anthropic_api_key secret file (worker only)"
        )
