"""Model provider adapters (ADR 0006 sections 1, 2 and 8).

``provider_for(settings)`` gives the worker's provider: ``AnthropicProvider`` with the
``anthropic_api_key`` secret when ``CLASSBEAT_MODEL_PROVIDER=anthropic`` (the default), and
``FakeProvider`` with ``CLASSBEAT_FAKE_MODEL_SCRIPT`` when it is ``fake`` (never in prod:
``check_worker_model_provider`` refuses it at start, and so does this). Only the worker builds a
provider; the web app never imports this package.

``CLASSBEAT_MODEL_PROVIDER`` stays ``anthropic | fake`` (ADR 0012 §5): OpenAI and DeepSeek are
listed in ``classbeat.agent.catalog`` and reached only through a route (M3c G1, G2), never
through this setting. Their key files (``openai_api_key``, ``deepseek_api_key``) are read
by ``classbeat.models.providers.provider_for_choice`` only. ``OpenAIProvider`` (M3c P2,
``openai_responses``) is built there, for the live suite run (M3c EV5) and, through a route, for
jobs; its catalog entries are ``owner_pending``. ``deepseek_provider(settings)`` (M3c P3) is the
one way to build ``DeepSeekProvider`` for a job or an eval run (G1's ``provider_for_choice`` calls
it): it refuses unless ``CLASSBEAT_DEEPSEEK_ENABLED`` is on and the ``deepseek_api_key`` file holds
a key, and with DeepSeek off its host is not on the worker allowlist either
(``worker_model_providers``).
The eval command alone may pass ``allow_disabled=True`` (see the function).
"""

from typing import Any

from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers.anthropic import AnthropicProvider
from classbeat.agent.providers.deepseek import DeepSeekProvider
from classbeat.agent.providers.fake import FakeProvider, FakeScript, ScriptMismatch
from classbeat.agent.providers.openai_responses import OpenAIProvider
from classbeat.config import Settings, model_sdk_environment_problems


class ProviderUnavailable(Exception):
    """No provider can be built from these settings; the message names the rule only."""


def provider_for(settings: Settings) -> ModelProvider:
    if settings.model_provider == "fake":
        if settings.environment == "prod":
            raise ProviderUnavailable("model_provider fake is not allowed when environment is prod")
        if settings.fake_model_script is None:
            raise ProviderUnavailable("model_provider fake needs fake_model_script")
        return FakeProvider.from_file(settings.fake_model_script)
    if settings.anthropic_api_key is None:
        raise ProviderUnavailable("model_provider anthropic needs the anthropic_api_key file")
    refused = model_sdk_environment_problems()  # also refused at worker start
    if refused:
        raise ProviderUnavailable("the worker must not set " + ", ".join(refused))
    return AnthropicProvider(settings.anthropic_api_key, turn_seconds=settings.agent_stage_seconds)


def deepseek_provider(
    settings: Settings, *, allow_disabled: bool = False, **kw: Any
) -> DeepSeekProvider:
    """``DeepSeekProvider`` for a job or an eval run, only while DeepSeek is on (ADR 0012 §2).

    ``allow_disabled=True`` is the **eval-only** path (Architect, 2026-10-31, keeping ADR 0012 §2
    as written): the owner-authorized live eval of a disabled DeepSeek model, on synthetic data,
    does not need ``CLASSBEAT_DEEPSEEK_ENABLED``. The key file and the SDK environment checks
    still apply. Jobs never pass it (``RouteModels``; ``resolve_route`` refuses a DeepSeek route
    while the setting is off), and only ``classbeat.evals`` may
    (``tests/test_models_jobs_guard.py``). ``kw`` (``transport`` and the like) is for tests."""
    if not settings.deepseek_enabled and not allow_disabled:
        raise ProviderUnavailable("DeepSeek is off (CLASSBEAT_DEEPSEEK_ENABLED)")
    if settings.deepseek_api_key is None:
        raise ProviderUnavailable("DeepSeek needs the deepseek_api_key file")
    refused = model_sdk_environment_problems()
    if refused:
        raise ProviderUnavailable("the worker must not set " + ", ".join(refused))
    return DeepSeekProvider(
        settings.deepseek_api_key, turn_seconds=settings.agent_stage_seconds, **kw
    )


__all__ = [
    "AnthropicProvider",
    "DeepSeekProvider",
    "FakeProvider",
    "FakeScript",
    "OpenAIProvider",
    "ProviderUnavailable",
    "ScriptMismatch",
    "deepseek_provider",
    "provider_for",
]
