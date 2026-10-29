"""Model provider adapters (ADR 0006 sections 1, 2 and 8).

``provider_for(settings)`` gives the worker's provider: ``AnthropicProvider`` with the
``anthropic_api_key`` secret when ``CLASSBEAT_MODEL_PROVIDER=anthropic`` (the default), and
``FakeProvider`` with ``CLASSBEAT_FAKE_MODEL_SCRIPT`` when it is ``fake`` (never in prod:
``check_worker_model_provider`` refuses it at start, and so does this). Only the worker builds a
provider; the web app never imports this package.
"""

from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers.anthropic import AnthropicProvider
from classbeat.agent.providers.fake import FakeProvider, FakeScript, ScriptMismatch
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


__all__ = [
    "AnthropicProvider",
    "FakeProvider",
    "FakeScript",
    "ProviderUnavailable",
    "ScriptMismatch",
    "provider_for",
]
