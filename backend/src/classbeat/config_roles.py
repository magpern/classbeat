"""Start checks per process role (worker or web app) and the model-key rules they enforce.

Which secret files and model SDK variables each process may see (ADR 0006 §9, ADR 0012 §2,
ADR 0015 section 4, ADR 0025 section 7). Re-exported from ``classbeat.config``, so callers keep
importing them from there.
"""

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from classbeat.config import Settings

# Each model provider's key file (ADR 0006 §9, ADR 0012 §2): worker only, never the web app.
MODEL_KEY_FIELDS = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
}


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


def worker_model_providers(settings: "Settings") -> frozenset[str]:
    """The model providers the worker can use (ADR 0012 §2): each one whose key file holds a
    key, DeepSeek only with ``deepseek_enabled`` too. Their hosts, and no other model host, go on
    the worker's allowlist (``classbeat.egress.worker_allowlist``)."""
    usable = {provider for provider in MODEL_KEY_FIELDS if settings.model_key(provider) is not None}
    if not settings.deepseek_enabled:
        usable.discard("deepseek")
    return frozenset(usable)


def check_worker_model_provider(settings: "Settings") -> None:
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


def check_worker_has_no_swedebeat_app(settings: "Settings") -> None:
    """Swedebeat's app key is the web app's only (ADR 0015 section 4): no worker job signs in to
    Swedebeat, so a readable ``swedebeat_app`` file refuses the worker's start."""
    if settings.swedebeat_app is not None:
        raise ModelProviderRefused(
            "the worker must not see the swedebeat_app secret file (web only)"
        )


def check_worker_has_no_turnstile_secret(settings: "Settings") -> None:
    """The Turnstile secret is the web app's only (ADR 0025 section 7): the worker verifies no
    token, so a readable ``turnstile_secret_key`` file refuses the worker's start."""
    if settings.turnstile_secret_key is not None:
        raise ModelProviderRefused(
            "the worker must not see the turnstile_secret_key secret file (web only)"
        )


def check_web_has_no_model_key(settings: "Settings") -> None:
    """The web app never calls a model, so it never holds a model key (ADR 0006 §9, ADR 0012
    §2): any readable model key file refuses the start."""
    for provider, field in MODEL_KEY_FIELDS.items():
        if settings.model_key(provider) is not None:
            raise ModelProviderRefused(
                f"the web app must not see the {field} secret file (worker only)"
            )
