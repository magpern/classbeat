"""``provider_for_choice(settings, choice)``: the provider for a catalog choice (ADR 0012 §4.4, §5).

Worker only (the live eval command and, from G2, the model jobs): it imports the provider
adapters, which the web process never loads, so ``classbeat.models`` does not import this module.

It builds only a **catalog** model with an effort its entry lists, never ``fake`` (``fake`` is not
in the catalog: it stays the worker's ``CLASSBEAT_MODEL_PROVIDER`` for dev and CI and bypasses
routes), and only with the provider's key file present and none of the refused model SDK
variables set. It checks neither ``data_policy`` nor ``CLASSBEAT_DEEPSEEK_ENABLED``: the live eval
of an ``owner_pending`` or disabled model runs on synthetic data with the owner's go-ahead (ADR 0012
§2, §6); ``resolve_route`` checks both for a job's route before this is called. Anthropic and
OpenAI (M3c P2) build their adapters; DeepSeek has none yet (M3c P3) and is refused here.
"""

from classbeat.agent import catalog
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers import AnthropicProvider, OpenAIProvider, ProviderUnavailable
from classbeat.config import Settings, model_sdk_environment_problems
from classbeat.models.routes import ModelChoice


def provider_for_choice(settings: Settings, choice: ModelChoice) -> ModelProvider:
    """The provider for ``choice`` (see the module); ``ProviderUnavailable`` naming the rule."""
    entry = catalog.find(choice.provider, choice.model)
    if entry is None:
        raise ProviderUnavailable("the provider and model are not in the catalog")
    if choice.effort not in entry.efforts:
        raise ProviderUnavailable("the catalog entry does not list that effort")
    key = settings.model_key(entry.provider)
    if key is None:
        raise ProviderUnavailable(f"provider {entry.provider} needs its key file")
    refused = model_sdk_environment_problems()
    if refused:
        raise ProviderUnavailable("the worker must not set " + ", ".join(refused))
    if entry.provider == "anthropic":
        return AnthropicProvider(key, turn_seconds=settings.agent_stage_seconds)
    if entry.provider == "openai":
        return OpenAIProvider(key, turn_seconds=settings.agent_stage_seconds)
    raise ProviderUnavailable(f"provider {entry.provider} has no adapter yet")
