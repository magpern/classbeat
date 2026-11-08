"""Application settings, read from the environment (prefix ``CLASSBEAT_``) and secret files.

Every ``SecretStr`` field, the secret-file registries and the checks that combine fields live
here; the non-secret field groups are pure mixins in ``config_accounts``, ``config_mail``,
``config_jobs``, ``config_agent`` and ``config_features`` (#812).
"""

import ssl
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from classbeat.config_accounts import (  # noqa: F401 (re-exported from classbeat.config)
    CONNECTION_KEK_ID,
    AccountSettings,
)
from classbeat.config_agent import AgentSettings
from classbeat.config_features import FeatureSettings
from classbeat.config_hosts import _is_local_host, check_prod_trusted_proxies
from classbeat.config_jobs import JobSettings
from classbeat.config_mail import (  # noqa: F401 (re-exported from classbeat.config)
    SUPPORT_ADDRESS,
    MailSettings,
)
from classbeat.config_passkeys import check_settings as check_passkey_settings
from classbeat.config_roles import (  # noqa: F401 (re-exported from classbeat.config)
    MODEL_KEY_FIELDS,
    MODEL_SDK_REFUSED_ENVIRONMENT,
    MODEL_SDK_REFUSED_PREFIXES,
    ModelProviderRefused,
    check_web_has_no_model_key,
    check_worker_has_no_swedebeat_app,
    check_worker_has_no_turnstile_secret,
    check_worker_model_provider,
    model_sdk_environment_problems,
    worker_model_providers,
)
from classbeat.config_secrets import (  # noqa: F401 (re-exported from classbeat.config)
    DEFAULT_SECRETS_DIR,
    SecretFilesSettingsSource,
    WithoutSecretsSource,
    logger,
    read_files,
    secrets_dir,
)
from classbeat.config_swedebeat import swedebeat_app_keys
from classbeat.config_turnstile import (  # noqa: F401 (re-exported from classbeat.config)
    TURNSTILE_DUMMY_KEY,
    TURNSTILE_SITE_KEY,
)
from classbeat.config_turnstile import check_secret_key as check_turnstile_secret_key
from classbeat.config_turnstile import check_settings as check_turnstile_settings

DATABASE_DRIVER = "postgresql+psycopg"
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
    "turnstile_secret_key",
)
# Secret files the app runs without (the feature is off): an unreadable one is not set, with a
# warning naming the field (#511). Every other secret file that exists but cannot be read is fatal.
OPTIONAL_SECRET_FILE_FIELDS = frozenset(
    {
        "smtp_password",
        "oauth_google_client_secret",
        "oauth_github_client_secret",
        "oauth_microsoft_client_secret",
        "swedebeat_app",
        "turnstile_secret_key",
    }
)
# Read by the SQL init scripts too, with the same bytes (tests/test_roles_script.py): kept exact.
BYTE_EXACT_SECRET_FIELDS = frozenset(
    {"database_password", "database_owner_password", "database_maint_password"}
)
MIN_APP_SECRET_BYTES = 32


def read_secret_files(
    directory: Path, names: tuple[str, ...], optional: frozenset[str] = frozenset()
) -> dict[str, str]:
    """``classbeat.config_secrets.read_files`` with the database passwords read byte for byte
    (``BYTE_EXACT_SECRET_FIELDS``): missing files are skipped, an unreadable one of a name in
    ``optional`` is not set (with a warning naming the field), any other unreadable one raises."""
    return read_files(directory, names, optional=optional, byte_exact=BYTE_EXACT_SECRET_FIELDS)


def _read_settings_secret_files() -> dict[str, str]:
    return read_secret_files(secrets_dir(), SECRET_FILE_FIELDS, OPTIONAL_SECRET_FILE_FIELDS)


class Settings(
    AccountSettings, MailSettings, JobSettings, AgentSettings, FeatureSettings, BaseSettings
):
    """All configuration of the web app and the worker.

    Everything that can carry a credential is a ``SecretStr``: it is masked in ``repr``,
    ``str``, ``model_dump`` and validation errors.
    """

    # Every ``SecretStr`` field is declared here, next to the registries above; the mixins hold
    # only non-secret fields (tests/test_config_field_groups.py). Every model validator stays
    # here too, so they run in the same order as before the split.

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

    # Accounts and sessions (ADR 0003; the other fields are in ``config_accounts``).
    # From the file ``app_secret_key`` (at least 32 bytes); the web app refuses to start without
    # it. All auth keys are derived from it (section 8).
    app_secret_key: SecretStr | None = None
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
    # Which enabled providers the sign-in and sign-up screens offer: a comma-separated list. The
    # default is Google only; GitHub and Microsoft stay fully built and are shown by adding them
    # here. Linking, re-authentication and the callbacks work for every enabled provider.
    oauth_visible_providers: str = "google"

    # From the file ``anthropic_api_key`` (never the environment), mounted on the worker only;
    # the web app refuses to start when it can read it. An empty file counts as no key
    # (``scripts/worktree.sh init`` creates an empty one; it is never a real key).
    anthropic_api_key: SecretStr | None = None
    # ADR 0012 §2: the OpenAI and DeepSeek keys, the same way (``openai_api_key`` and
    # ``deepseek_api_key`` files, worker only, empty file = none, never the environment).
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    # ADR 0015: the Swedebeat connection (its switch ``swedebeat_enabled`` is in
    # ``config_features``). Swedebeat's AES app key (JSON ``{"key", "iv"}``: 32 and 16
    # characters) comes from, in this order: the optional secret file ``swedebeat_app`` (a runtime
    # Docker secret, web only, never the environment; the home server's way) and the file
    # ``swedebeat_app_file`` that a release image build writes (the owner's GitHub secret
    # ``SWEDEBEAT_APP``, ADR 0015 section 4; absent from every PR, CI and e2e image).
    # With neither, connecting answers ``app_key_unavailable``.
    swedebeat_app: SecretStr | None = None
    # ADR 0025 (#721): Turnstile's secret key file (web only, never the environment), needed when
    # ``turnstile_enabled`` is on and ``turnstile_fake`` is off (``config_features``).
    turnstile_secret_key: SecretStr | None = None
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
            WithoutSecretsSource(env_settings, SECRET_FILE_FIELDS),
            SecretFilesSettingsSource(settings_cls, _read_settings_secret_files),
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

    @field_validator("turnstile_secret_key")
    @classmethod
    def _check_turnstile_secret_key(cls, value: SecretStr | None) -> SecretStr | None:
        return check_turnstile_secret_key(value)  # the empty placeholder becomes None

    @model_validator(mode="after")
    def _check_turnstile(self) -> "Settings":
        check_turnstile_settings(self)  # ADR 0025 section 7: no fake or test key in prod
        return self

    @property
    def turnstile_hostname(self) -> str:
        """The host a Turnstile answer must name (ADR 0025 section 4): ``public_base_url``'s."""
        return urlsplit(self.public_base_url).hostname or ""

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
    def _check_passkeys(self) -> "Settings":
        check_passkey_settings(self)  # in production a localhost RP ID is refused too
        return self

    @model_validator(mode="after")
    def _prod_needs_https(self) -> "Settings":
        if self.environment == "prod" and not self.public_base_url.startswith("https://"):
            raise ValueError("public_base_url must be https:// when environment is prod")
        return self

    @model_validator(mode="after")
    def _prod_trusts_only_private_proxies(self) -> "Settings":
        if self.environment == "prod":
            check_prod_trusted_proxies(self.trusted_proxy_networks)  # section 9, #495
        return self

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


def _with_password(url: SecretStr, password: SecretStr | None) -> URL:
    result = make_url(url.get_secret_value())
    if password is not None:
        result = result.set(password=password.get_secret_value())
    return result
