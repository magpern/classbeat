"""Billing provider settings: the webhook secret's format and the start-up gates (ADR 0032
section 4, #973).

Pure (no I/O). ``Settings`` calls these from its validators; ``billing_provider`` and
``billing_enabled`` themselves are in ``config_features``. The gates:

- ``paddle`` is refused (``config_features``): the real HTTP client is not written;
- ``fake_paddle`` is refused when ``environment == prod``: a fake can never take a payment there;
- a Paddle provider needs the ``paddle_webhook_secret`` file, so the webhook never runs unsigned;
- ``billing_enabled = true`` stays refused (``config_features``) until ``paddle`` is accepted.
"""

from typing import TYPE_CHECKING, Literal

from pydantic import SecretStr

if TYPE_CHECKING:
    from classbeat.config import Settings

MAX_SECRETS = 4  # paddle_signature.MAX_SECRETS: a rotation holds the old and the new


def secrets_of(value: SecretStr) -> list[str]:
    """The secret file's non-empty lines: one secret each."""
    return [line.strip() for line in value.get_secret_value().splitlines() if line.strip()]


def check_secret(value: SecretStr | None) -> SecretStr | None:
    """``None`` for the empty placeholder; otherwise 1 to 4 printable ASCII secrets."""
    if value is None or not value.get_secret_value().strip():
        return None
    found = secrets_of(value)
    # The message names the file, never its content.
    if not 1 <= len(found) <= MAX_SECRETS or any(
        len(s) > 256 or not s.isascii() or not s.isprintable() or " " in s for s in found
    ):
        raise ValueError("paddle_webhook_secret must hold 1 to 4 printable ASCII lines")
    return value


def provider_of(settings: "Settings") -> Literal["fake_paddle", "paddle"] | None:
    """The Paddle provider the webhook stores events as, or `None` with Dummy (no route)."""
    provider = settings.billing_provider
    return None if provider == "dummy" else provider


def webhook_secrets(settings: "Settings") -> list[str]:
    """The webhook's signing secrets (empty without the file)."""
    value = settings.paddle_webhook_secret
    return [] if value is None else secrets_of(value)


def check_settings(settings: "Settings") -> None:
    provider = settings.billing_provider
    if provider == "fake_paddle" and settings.environment == "prod":
        raise ValueError("billing_provider fake_paddle is not allowed when environment is prod")
    if provider != "dummy" and settings.paddle_webhook_secret is None:
        raise ValueError(
            f"billing_provider {provider} needs the paddle_webhook_secret secret file (it is empty)"
        )
