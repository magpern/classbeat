"""Application settings, read from the environment (prefix ``CLASSBEAT_``) and secret files."""

import ipaddress
import json
import os
import re
import ssl
from collections.abc import Mapping
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
    "database_maint_password",
    "app_secret_key",
    "smtp_password",
    "oauth_google_client_secret",
    "oauth_github_client_secret",
    "oauth_microsoft_client_secret",
    "anthropic_api_key",
    "openai_api_key",
    "deepseek_api_key",
    "swedebeat_app",
)
# Each model provider's key file (ADR 0006 §9, ADR 0012 §2): worker only, never the web app.
MODEL_KEY_FIELDS = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
}
# Hosts where unencrypted SMTP (``smtp_tls = none``) is allowed: loopback and the compose service.
PLAIN_SMTP_HOSTS = frozenset({"localhost", "mailpit"})
MIN_APP_SECRET_BYTES = 32
# ADR 0004 section 2: the id of the current KEK (the ``connection_kek`` secret file).
CONNECTION_KEK_ID = re.compile(r"kek-[a-z0-9-]{1,28}")


def swedebeat_app_keys(value: SecretStr) -> dict[str, str] | None:
    """The ``swedebeat_app`` file's key and iv if it is a JSON object whose ``key`` is 32 and
    ``iv`` 16 single-byte characters (the library encodes both as latin-1); else ``None``."""
    try:
        data = json.loads(value.get_secret_value())
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != {"key", "iv"}:  # pyright: ignore[reportUnknownArgumentType]
        return None
    key, iv = data["key"], data["iv"]
    if not isinstance(key, str) or not isinstance(iv, str):
        return None
    try:
        sized = len(key.encode("latin-1")) == 32 and len(iv.encode("latin-1")) == 16
    except UnicodeEncodeError:
        return None
    return {"key": key, "iv": iv} if sized else None


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
    # The admin role (``classbeat_maint``, ADR 0011 section 5): web only, never the worker. The
    # admin area works only with both the URL and the ``database_maint_password`` file; without
    # either its routes answer 503 ``admin_unavailable``.
    maint_database_url: SecretStr | None = None
    database_maint_password: SecretStr | None = None
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
    # Opt-in: a PEM certificate (or CA) file, mounted read-only, that the SMTP server's
    # certificate must chain to instead of the system store (a self-signed Proton Mail Bridge).
    # Verification and the hostname check stay on. Unset: the system store.
    smtp_ca_file: Path | None = None

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
    # Agent runs (ADR 0006 sections 2, 5 and 8; classbeat.agent). The model and its request.
    agent_model: str = Field(default="claude-opus-5-5", pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    agent_max_tokens: int = Field(default=32_000, ge=1_000, le=128_000)
    # Limits per stage run, and the cost ceiling per job across stages and attempts.
    agent_max_turns: int = Field(default=12, ge=1, le=100)
    agent_max_tool_calls: int = Field(default=40, ge=1, le=500)
    agent_max_corrections: int = Field(default=3, ge=0, le=20)
    agent_stage_seconds: float = Field(default=300.0, gt=0, le=3600)
    agent_job_cost_micros: int = Field(default=1_500_000, ge=0, le=100_000_000)  # $1.50
    # The song-picking kinds' own ceiling across their stages and attempts (ADR 0008 section 3).
    song_job_cost_micros: int = Field(default=3_000_000, ge=0, le=100_000_000)  # $3
    # Output tokens reserved per turn by the cost check (thinking included).
    agent_turn_output_reserve: int = Field(default=8_000, ge=0, le=128_000)
    # The FakeProvider's script (a JSON file) when model_provider is fake.
    fake_model_script: Path | None = None

    # Jobs (ADR 0005 sections 3 and 4).
    job_lease_seconds: int = Field(default=120, ge=30, le=600)
    # The worker's engine pool follows it (classbeat.worker.loop.worker_pool: 3 * n + 2 at most).
    worker_concurrency: int = Field(default=2, ge=1, le=16)
    # Reap, expire and settle (ADR 0005 section 3: every 30 s).
    worker_maintenance_seconds: float = Field(default=30.0, gt=0)
    # On SIGTERM, how long runs may take to reach a checkpoint or return from a call before
    # their leases are handed back (inside compose's 30 s stop_grace_period).
    worker_shutdown_wait_seconds: float = Field(default=25.0, ge=0, le=25)
    approval_ttl_hours: int = Field(default=168, ge=1, le=8760)
    # operation_busy requeues per phase that cost no attempt; after that each one counts (#86).
    job_busy_requeue_limit: int = Field(default=5, ge=0, le=100)
    # Queued and running jobs per user; creation and approval above it are 429 (ADR 0005 s. 2).
    jobs_per_user: int = Field(default=2, ge=1, le=100)
    # Revising a class by chat (ADR 0007 section 4): chat revisions a class may have (the first
    # design is not counted), user requests stored per class, and how many earlier requests
    # along the revision chain go back to the model.
    revisions_per_class: int = Field(default=25, ge=1, le=200)
    # ADR 0011 section 4: the environment's upper bound on a trial's length. A stored
    # ``trial.days`` above it is clamped when a trial starts; 0 turns trials off.
    trial_days_max: int = Field(default=30, ge=0, le=366)
    # ADR 0011 section 4: the environment's upper bounds on every plan's money limits; a plan's
    # effective ``classes_per_month`` and ``monthly_cost_micros`` are the smaller of the row
    # and these. ``CLASSBEAT_JOBS_PER_USER`` and ``CLASSBEAT_REVISIONS_PER_CLASS`` bound the
    # plan's concurrent jobs and revisions per class alike.
    classes_per_month_max: int = Field(default=200, ge=0, le=10_000)
    monthly_cost_micros_max: int = Field(default=300_000_000, ge=0, le=100_000_000_000)
    chat_max_requests: int = Field(default=50, ge=1, le=200)
    chat_context_requests: int = Field(default=10, ge=1, le=30)
    # ADR 0007 section 8: how long a new MOWL workout may take to appear in My Workouts. Unset
    # (the default), an uncertain save that left no new program stays uncertain for the user;
    # set (by the owner, after measuring it at live activation), such a save whose call returned
    # at least this long ago is ``failed``. A day at most.
    mowl_listing_lag_seconds: float | None = Field(default=None, gt=0, le=86400)
    # Song picking and the Spotify connection (ADR 0008, ADR 0009). Off by default: the owner
    # switches live features on (roadmap Amendment 1); off, every Spotify route answers 404
    # ``feature_off``. A connect flow lives at most 10 minutes (the table's check), a token is
    # refreshed when it has less than the margin left, and a refresh request (inside the row
    # lock) ends within its timeout, below the role's 30 s statement and 60 s idle limits.
    song_picking: bool = False
    spotify_flow_ttl_seconds: int = Field(default=600, ge=60, le=600)
    spotify_refresh_margin_seconds: int = Field(default=120, ge=0, le=1800)
    spotify_refresh_timeout_seconds: float = Field(default=10.0, gt=0, le=20)
    # ADR 0008 section 2: how long a new Spotify playlist may take to appear in the user's
    # playlist listing. Unset (the default), an uncertain create whose listing shows no playlist
    # with its ``ref`` stays uncertain; set (by the owner, after measuring it at live activation),
    # such a create whose call returned at least this long ago is ``failed``. A day at most.
    spotify_listing_lag_seconds: float | None = Field(default=None, gt=0, le=86400)
    # ADR 0009 section 8: the Spotify requests one job may send across its stages and attempts
    # (the token refreshes included); the request over it is never sent (``limit_exceeded``).
    spotify_calls_per_job: int = Field(default=300, ge=1, le=10_000)
    # ADR 0008 section 2: how long a pick waits for MOWL's track list of the pool playlist to
    # show exactly the candidates just written (``pool_not_synced`` after it).
    spotify_pool_sync_wait_seconds: float = Field(default=120.0, ge=0, le=600)
    # From the file ``anthropic_api_key`` (never the environment), mounted on the worker only;
    # the web app refuses to start when it can read it. An empty file counts as no key
    # (``scripts/worktree.sh init`` creates an empty one; it is never a real key).
    anthropic_api_key: SecretStr | None = None
    # ADR 0012 §2: the OpenAI and DeepSeek keys, the same way (``openai_api_key`` and
    # ``deepseek_api_key`` files, worker only, empty file = none, never the environment).
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    # ADR 0012 §2: off, the DeepSeek host is not on the worker's list and no DeepSeek provider is
    # built for a job. The owner turns it on only after ADR 0012 §6 holds.
    deepseek_enabled: bool = False
    # ADR 0015: the Swedebeat connection. ``swedebeat_enabled`` is the environment's kill switch
    # (off by default; the admin flag ``swedebeat_enabled`` can only tighten it, ADR 0011 section
    # 7). Swedebeat's AES app key (JSON ``{"key", "iv"}``: 32 and 16 characters) comes from, in
    # this order: the optional secret file ``swedebeat_app`` (a runtime Docker secret, web only,
    # never the environment; the home server's way) and the file ``swedebeat_app_file`` that a
    # release image build writes (the owner's GitHub secret ``SWEDEBEAT_APP``, ADR 0015 section
    # 4; absent from every PR, CI and e2e image). With neither, connecting answers
    # ``app_key_unavailable``.
    swedebeat_enabled: bool = False
    swedebeat_app: SecretStr | None = None
    swedebeat_app_file: Path = Path("/app/swedebeat_app.json")

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

    @field_validator("database_url", "migrations_database_url", "maint_database_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr | None, info: ValidationInfo) -> SecretStr | None:
        if value is None:
            return None
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

    @field_validator("anthropic_api_key", "openai_api_key", "deepseek_api_key")
    @classmethod
    def _check_model_api_key(
        cls, value: SecretStr | None, info: ValidationInfo
    ) -> SecretStr | None:
        if value is None:
            return None
        key = value.get_secret_value()
        if not key.strip():
            return None  # the empty placeholder: no key
        # It goes into an HTTP header: no whitespace or control characters anywhere.
        if not key.isascii() or any(c.isspace() or not c.isprintable() for c in key):
            raise ValueError(f"{info.field_name} has whitespace, control or non-ASCII characters")
        return value

    @field_validator("swedebeat_app")
    @classmethod
    def _check_swedebeat_app(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None  # the empty placeholder: no key on this server
        if swedebeat_app_keys(value) is None:
            # The message names the file, never its content.
            raise ValueError(
                "swedebeat_app must be JSON with a 32-character key and a 16-character iv"
            )
        return value

    def swedebeat_keys(self) -> dict[str, str] | None:
        """``{"key", "iv"}`` for the library's ``Credentials.swedebeat_app``, or ``None``: the
        runtime secret first, else the file a release image holds (read per call; a missing,
        unreadable or malformed file is no key)."""
        if self.swedebeat_app is not None:
            return swedebeat_app_keys(self.swedebeat_app)
        try:
            text = self.swedebeat_app_file.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        return swedebeat_app_keys(SecretStr(text))

    def model_key(self, provider: str) -> SecretStr | None:
        """The key file of ``provider`` (``None`` when missing or empty, or not a provider)."""
        field = MODEL_KEY_FIELDS.get(provider)
        return None if field is None else getattr(self, field)

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

    @field_validator("smtp_ca_file", mode="before")
    @classmethod
    def _empty_smtp_ca_file_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _check_smtp_ca_file(self) -> "Settings":
        path = self.smtp_ca_file
        if path is None:
            return self
        if self.smtp_tls == "none":
            raise ValueError("smtp_ca_file cannot be combined with smtp_tls=none")
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=str(path))
        except (OSError, ssl.SSLError):
            # The path and the OS error text stay out of the message.
            raise ValueError("smtp_ca_file must be a readable PEM certificate file") from None
        return self

    @field_validator("smtp_port")
    @classmethod
    def _check_smtp_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("smtp_port must be between 1 and 65535")
        return value

    @model_validator(mode="after")
    def _spotify_needs_a_loopback_address(self) -> "Settings":
        # ADR 0009 section 2: Spotify does not accept ``localhost`` in a redirect URI (only the
        # loopback address), so the fixed callback would never work.
        if self.song_picking and (urlsplit(self.public_base_url).hostname or "") == "localhost":
            raise ValueError(
                "public_base_url may not be localhost with song_picking on (use 127.0.0.1)"
            )
        return self

    @property
    def spotify_redirect_uri(self) -> str:
        """The fixed callback URL (ADR 0009 section 2): from settings only, never from input."""
        return f"{self.public_base_url}/oauth/spotify/callback"

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

    def maint_sqlalchemy_url(self) -> URL | None:
        """The admin role's URL with its password, or ``None`` when either is missing (an empty
        password file counts as none). Only ``classbeat.admin.db`` may call it (guard 11)."""
        password = self.database_maint_password
        if self.maint_database_url is None or password is None or not password.get_secret_value():
            return None
        return _with_password(self.maint_database_url, password)


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


# The model SDKs read keys, base URLs, organisation and project ids, extra headers and debug
# logging of request bodies from variables with these prefixes, even with an explicit key and
# base URL. classbeat passes everything explicitly, so any such variable is a misconfiguration or
# a leak path (ADR 0012 §2; ADR 0006 §9 named ``ANTHROPIC_LOG`` and ``ANTHROPIC_CUSTOM_HEADERS``).
# Matched case-insensitively (Windows environment names are). ``SSLKEYLOGFILE`` makes
# ``ssl.create_default_context()`` write TLS session secrets to a file (enough to decrypt the
# model key in a capture); the worker's own TLS contexts ignore it
# (``classbeat.egress.tls_client_context``), and it is refused too in case any other does (#121).
MODEL_SDK_REFUSED_PREFIXES = ("ANTHROPIC_", "OPENAI_", "DEEPSEEK_")
MODEL_SDK_REFUSED_ENVIRONMENT = ("SSLKEYLOGFILE",)


def model_sdk_environment_problems(environ: Mapping[str, str] | None = None) -> list[str]:
    """The names (never the values) of the refused model SDK variables that are set, in
    ``environ`` (the process environment by default), whatever their case."""
    return sorted(
        name
        for name in (os.environ if environ is None else environ)
        if name.upper().startswith(MODEL_SDK_REFUSED_PREFIXES)
        or name.upper() in MODEL_SDK_REFUSED_ENVIRONMENT
    )


def worker_model_providers(settings: Settings) -> frozenset[str]:
    """The model providers the worker can use (ADR 0012 §2): each one whose key file holds a
    key, DeepSeek only with ``deepseek_enabled`` too. Their hosts, and no other model host, go on
    the worker's allowlist (``classbeat.egress.worker_allowlist``)."""
    usable = {provider for provider in MODEL_KEY_FIELDS if settings.model_key(provider) is not None}
    if not settings.deepseek_enabled:
        usable.discard("deepseek")
    return frozenset(usable)


def check_worker_model_provider(settings: Settings) -> None:
    """ADR 0006 §9, ADR 0012 §2: ``anthropic`` needs the ``anthropic_api_key`` file; with a real
    provider (``anthropic``, or any model key file present) none of the refused model SDK
    variables may be set; ``fake`` never in prod."""
    if settings.model_provider == "anthropic" and settings.anthropic_api_key is None:
        raise ModelProviderRefused(
            "model_provider is anthropic but the anthropic_api_key secret file is missing or empty"
        )
    refused = model_sdk_environment_problems()
    real = settings.model_provider == "anthropic" or any(
        settings.model_key(provider) is not None for provider in MODEL_KEY_FIELDS
    )
    if real and refused:
        raise ModelProviderRefused(
            "a real model provider: the worker must not set " + ", ".join(refused)
        )
    if settings.model_provider == "fake" and settings.environment == "prod":
        raise ModelProviderRefused("model_provider fake is not allowed when environment is prod")


def check_worker_has_no_swedebeat_app(settings: Settings) -> None:
    """Swedebeat's app key is the web app's only (ADR 0015 section 4): no worker job signs in to
    Swedebeat, so a readable ``swedebeat_app`` file refuses the worker's start."""
    if settings.swedebeat_app is not None:
        raise ModelProviderRefused(
            "the worker must not see the swedebeat_app secret file (web only)"
        )


def check_web_has_no_model_key(settings: Settings) -> None:
    """The web app never calls a model, so it never holds a model key (ADR 0006 §9, ADR 0012
    §2): any readable model key file refuses the start."""
    for provider, field in MODEL_KEY_FIELDS.items():
        if settings.model_key(provider) is not None:
            raise ModelProviderRefused(
                f"the web app must not see the {field} secret file (worker only)"
            )
