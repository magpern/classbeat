"""Account and session settings (ADR 0003), a field group of ``classbeat.config.Settings``.

Pure: no I/O. Never instantiated on its own: ``Settings`` inherits these fields and validators
and validates them with its own config (prefix ``CLASSBEAT_``, inputs hidden in errors).

A ``BaseSettings`` (not a plain ``BaseModel``) only so its ``model_config`` type matches
``Settings``'; the mixin adds no config of its own and declares no ``SecretStr`` field.
"""

import ipaddress
import re
from typing import Literal

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings

from classbeat.config_hosts import check_public_base_url, parse_networks
from classbeat.emailnorm import normalize_email

# ADR 0004 section 2: the id of the current KEK (the ``connection_kek`` secret file).
CONNECTION_KEK_ID = re.compile(r"kek-[a-z0-9-]{1,28}")


class AccountSettings(BaseSettings):
    """Accounts and sessions (ADR 0003): the public origin, admins, proxies and sign-up."""

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
    # ADR 0032 section 9: the one switch that opens sign-up to the public (off by default). On, the
    # environment's mode is ``open`` unless ``signup_mode`` is ``closed``; the database can still
    # tighten it, and in prod it stays ``invite`` while Turnstile is off (``flags.signup_policy``).
    # Declared before ``signup_mode``, whose validator reads it.
    public_signup: bool = False
    # Who may create an account (section 5): ``invite`` (default) or ``closed``; ``open`` is
    # accepted only together with ``public_signup`` (the flag, not this, opens sign-up).
    signup_mode: Literal["invite", "closed", "open"] = "invite"
    # The languages the operator enables, comma-separated bare codes (ADR 0023 section 1). ``en`` is
    # always on; a language is offered when it is enabled and in the supported manifest.
    locales: str = "en,sv,da"

    @field_validator("connection_kek_id")
    @classmethod
    def _check_connection_kek_id(cls, value: str) -> str:
        if CONNECTION_KEK_ID.fullmatch(value) is None:
            raise ValueError("connection_kek_id must match ^kek-[a-z0-9-]{1,28}$")
        return value

    @field_validator("signup_mode")
    @classmethod
    def _open_needs_public_signup(cls, value: str, info: ValidationInfo) -> str:
        if value == "open" and info.data.get("public_signup") is not True:
            raise ValueError("signup_mode=open needs CLASSBEAT_PUBLIC_SIGNUP=true")
        return value

    @field_validator("public_base_url")
    @classmethod
    def _check_public_base_url(cls, value: str) -> str:
        return check_public_base_url(value)

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

    @property
    def admin_email_set(self) -> frozenset[str]:
        """``admin_emails`` in the normalized form of ADR 0003 section 1 (ASCII lower case)."""
        return frozenset(normalize_email(e) for e in self.admin_emails.split(",") if e.strip())

    @property
    def enabled_locales(self) -> frozenset[str]:
        """``locales`` as a set of two-letter codes, with ``en`` always in; anything else in the
        list is dropped (the operator's typo never becomes a code)."""
        codes = {c.strip().lower() for c in self.locales.split(",")}
        return frozenset({c for c in codes if re.fullmatch(r"[a-z]{2}", c)} | {"en"})

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return parse_networks(self.trusted_proxies)
