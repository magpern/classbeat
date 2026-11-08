"""Mail settings (ADR 0003 section 5), a field group of ``classbeat.config.Settings``.

Pure: no I/O. The SMTP password (a secret file) and the checks that combine fields or read the CA
file stay on ``Settings`` in ``classbeat.config``. Never instantiated on its own.

A ``BaseSettings`` (not a plain ``BaseModel``) only so its ``model_config`` type matches
``Settings``'; the mixin adds no config of its own and declares no ``SecretStr`` field.
"""

import re
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings

# A bare address (no display name, no angle brackets, no whitespace or control characters).
SUPPORT_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")


class MailSettings(BaseSettings):
    """The SMTP server and the addresses mail is sent from and answered to."""

    # Mail (section 5). Unset ``smtp_host``: no mail is sent (a warning is logged instead).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_sender: str = "classbeat@localhost"
    # The bare address that replaces "the operator" in mail: the footer's ``mailto:`` and the
    # ``Reply-To`` (ADR 0022 section 3). An owner-confirmable default: the mailbox must exist
    # before deploy. The admin setting (ME2) overrides it.
    mail_support_address: str = "support@classbeat.eu"
    smtp_tls: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str | None = None
    # Opt-in: Bridge's own PEM leaf certificate, mounted read-only, that the SMTP server's
    # certificate must chain to instead of the system store (a self-signed Proton Mail Bridge).
    # Verification and the hostname check stay on. Unset: the system store.
    smtp_ca_file: Path | None = None

    @field_validator("mail_support_address")
    @classmethod
    def _check_mail_support_address(cls, value: str) -> str:
        if SUPPORT_ADDRESS.fullmatch(value) is None:
            raise ValueError("mail_support_address must be a bare email address")
        return value

    @field_validator("smtp_ca_file", mode="before")
    @classmethod
    def _empty_smtp_ca_file_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("smtp_port")
    @classmethod
    def _check_smtp_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("smtp_port must be between 1 and 65535")
        return value
