"""Model provider adapters (ADR 0006 sections 1, 2 and 8).

``provider_for(settings)`` gives the worker's provider: ``AnthropicProvider`` with the
``anthropic_api_key`` secret when ``CLASSBEAT_MODEL_PROVIDER=anthropic`` (the default), and
``FakeProvider`` with ``CLASSBEAT_FAKE_MODEL_SCRIPT`` when it is ``fake`` (never in prod:
``check_worker_model_provider`` refuses it at start, and so does this). Only the worker builds a
provider; the web app never imports this package.

``CLASSBEAT_MODEL_PROVIDER`` stays ``anthropic | fake`` (ADR 0012 §5): OpenAI and DeepSeek are
listed in ``classbeat.agent.catalog`` and reached only through a route (M3c G1, G2), never
through this setting. Their key files (``openai_api_key``, ``deepseek_api_key``) are read here
by no code yet. ``OpenAIProvider`` (M3c P2, ``openai_responses``) is exported for the routes and
the eval runner to build (M3c G1, EV5); nothing builds it yet, and its catalog entries are
``owner_pending``. ``deepseek_provider(settings)`` (M3c P3) is the one way to build
``DeepSeekProvider`` for a job or an eval run (G1's ``provider_for_choice`` calls it): it refuses
unless ``CLASSBEAT_DEEPSEEK_ENABLED`` is on and the ``deepseek_api_key`` file holds a key, and
with DeepSeek off its host is not on the worker allowlist either (``worker_model_providers``).
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


def deepseek_provider(settings: Settings, **kw: Any) -> DeepSeekProvider:
    """``DeepSeekProvider`` for a job or an eval run, only while DeepSeek is on (ADR 0012 §2).
    ``kw`` (``transport`` and the like) is for tests."""
    if not settings.deepseek_enabled:
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
