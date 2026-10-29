"""Model provider adapters (ADR 0006 sections 1, 2 and 8).

``provider_for(settings)`` gives the worker's provider: ``FakeProvider`` with
``CLASSBEAT_FAKE_MODEL_SCRIPT`` when ``CLASSBEAT_MODEL_PROVIDER=fake`` (never in prod:
``check_worker_model_provider`` refuses it at start). The ``AnthropicProvider`` comes with A2;
until then ``anthropic`` raises ``ProviderUnavailable``.
"""

from classbeat.agent.providers.fake import FakeProvider, FakeScript, ScriptMismatch
from classbeat.config import Settings


class ProviderUnavailable(Exception):
    """No provider can be built from these settings; the message names the rule only."""


def provider_for(settings: Settings) -> FakeProvider:
    if settings.model_provider == "fake":
        if settings.environment == "prod":
            raise ProviderUnavailable("model_provider fake is not allowed when environment is prod")
        if settings.fake_model_script is None:
            raise ProviderUnavailable("model_provider fake needs fake_model_script")
        return FakeProvider.from_file(settings.fake_model_script)
    raise ProviderUnavailable("the anthropic provider is not available yet (A2)")


__all__ = ["FakeProvider", "FakeScript", "ProviderUnavailable", "ScriptMismatch", "provider_for"]
