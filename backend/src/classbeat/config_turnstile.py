"""Turnstile settings: the key formats and their start-up checks (ADR 0025 section 7).

Pure (no I/O). ``Settings`` calls these from its validators, so the messages and the order of
the checks are those of ``classbeat.config``. Re-exported from ``classbeat.config``.
"""

import re
from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    from classbeat.config import Settings

# Cloudflare's Turnstile test keys (ADR 0025 section 7): a digit, ``x``, zeros and two capitals
# (``1x00000000000000000000AA`` and the like). A pattern, not a list, so a test key Cloudflare adds
# later is refused in prod too.
TURNSTILE_DUMMY_KEY = re.compile(r"[0-9]x0{16,}[A-Z]{2}")
# A Turnstile site key is public; it goes into the session answer and the widget's options.
TURNSTILE_SITE_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")


def check_site_key(value: str | None) -> str | None:
    """The public site key, if set, has the allowed characters and length."""
    if value is not None and TURNSTILE_SITE_KEY.fullmatch(value) is None:
        raise ValueError("turnstile_site_key must be 1 to 128 of A-Z, a-z, 0-9, _ and -")
    return value


def check_secret_key(value: SecretStr | None) -> SecretStr | None:
    """The secret file's key, ``None`` for the empty placeholder (so Turnstile cannot be on)."""
    if value is None or not value.get_secret_value().strip():
        return None
    key = value.get_secret_value()
    # The message names the file, never its content.
    bad = any(c.isspace() or not c.isprintable() for c in key)
    if len(key) > 1024 or not key.isascii() or bad:
        raise ValueError("turnstile_secret_key has whitespace, control or non-ASCII characters")
    return value


def check_settings(settings: "Settings") -> None:
    """In prod the fake is refused whatever ``turnstile_enabled`` says (as the fake model provider
    is), and so is a Cloudflare test key: it always passes (or always fails) and would answer for
    example.com. On (and not the fake), both keys are needed."""
    if settings.environment == "prod":
        if settings.turnstile_fake:
            raise ValueError("turnstile_fake is not allowed when environment is prod")
        secret = settings.turnstile_secret_key
        for name, value in (
            ("turnstile_site_key", settings.turnstile_site_key),
            ("turnstile_secret_key", None if secret is None else secret.get_secret_value()),
        ):
            if value is not None and TURNSTILE_DUMMY_KEY.fullmatch(value) is not None:
                raise ValueError(f"{name} is a Cloudflare test key, not allowed in prod")
    if settings.turnstile_enabled and not settings.turnstile_fake:
        if settings.turnstile_site_key is None:
            raise ValueError("turnstile_enabled needs CLASSBEAT_TURNSTILE_SITE_KEY")
        if settings.turnstile_secret_key is None:
            raise ValueError(
                "turnstile_enabled needs the turnstile_secret_key secret file (it is empty)"
            )
