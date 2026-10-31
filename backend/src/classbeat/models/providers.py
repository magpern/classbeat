"""The worker's model providers for a catalog choice (ADR 0012 §4.4, §5).

Worker only (the live eval command and the model jobs): it imports the provider adapters, which
the web process never loads, so ``classbeat.models`` does not import this module.

- ``provider_for_choice(settings, choice)`` builds only a **catalog** model with an effort its
  entry lists, never ``fake`` (``fake`` is not in the catalog: it stays the worker's
  ``CLASSBEAT_MODEL_PROVIDER`` for dev and CI and bypasses routes), and only with the provider's
  key file present and none of the refused model SDK variables set. It checks neither
  ``data_policy`` nor ``CLASSBEAT_DEEPSEEK_ENABLED``: the live eval of an ``owner_pending`` or
  disabled model runs on synthetic data with the owner's go-ahead (ADR 0012 §2, §6);
  ``resolve_route`` checks both for a job's route before this is called. Anthropic and OpenAI
  (``OpenAIProvider``, M3c P2) are built; DeepSeek has no adapter on this branch yet (M3c P3), so
  its choices are refused here until it lands.
- ``RouteModels(settings)`` is the model jobs' ``ModelSource`` (``classbeat.models.jobs``): with
  ``fake`` it answers the fake provider and the agent config unchanged (no route, no database
  read); otherwise ``resolve_route`` for the job's route key and group, inside the job's fence,
  then ``provider_for_choice``, and the agent config with the choice's model, effort, route key
  and eval result. ``RouteUnusable`` and every failure to build are ``model_config`` before any
  request. ``build`` is the seam the tests replace with a replay provider (it still receives
  only the resolved choice).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from classbeat.agent import catalog
from classbeat.agent.loop import AgentConfig
from classbeat.agent.provider import ModelProvider
from classbeat.agent.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderUnavailable,
    provider_for,
)
from classbeat.config import Settings, model_sdk_environment_problems
from classbeat.jobs.errors import JobFailure
from classbeat.jobs.run import JobRun
from classbeat.models.jobs import GroupModel
from classbeat.models.routes import ModelChoice, resolve_route
from classbeat.models.store import StageGroup


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


@dataclass(frozen=True)
class RouteModels:
    """The worker's ``ModelSource`` (see the module)."""

    settings: Settings
    build: Callable[[Settings, ModelChoice], ModelProvider] = provider_for_choice

    async def __call__(
        self, run: JobRun, route_key: str, group: StageGroup, agent: AgentConfig
    ) -> GroupModel:
        if self.settings.model_provider == "fake":  # dev and CI; refused in prod by provider_for
            try:
                return GroupModel(provider_for(self.settings), agent)
            except Exception:  # ProviderUnavailable, a missing script: an operator problem
                raise JobFailure("model_config") from None
        async with run.fenced() as fs:
            choice = await resolve_route(fs.db, route_key, group, settings=self.settings)
        try:
            provider = self.build(self.settings, choice)
        except Exception:  # ProviderUnavailable and the like: an operator problem
            raise JobFailure("model_config") from None
        return GroupModel(
            provider,
            replace(
                agent,
                model=choice.model,
                effort=choice.effort,
                route_key=choice.route_key,
                eval_result_id=choice.eval_result_id,
            ),
        )
