"""What a model job's stage group runs on (ADR 0012 section 5, "Resolving"; M3c G2).

Every model job (``design_draft``, ``revise_draft``, ``design_with_songs``, ``revise_songs``)
asks its runtime's ``ModelSource`` once per stage group, at the start of that group's work in an
attempt and before any MOWL or Spotify call of it, with the job's ``route_key`` (fixed in the
job input at creation, ``classbeat.jobs.inputs.ModelJobInput``). The answer is a ``GroupModel``:
a **new** provider for this group of this attempt (never shared between jobs or attempts) and the
agent config with the choice's model, effort, ``route_key`` and ``eval_result_id``, so every
``agent_runs`` row and the revision's provenance record where the model came from.

The worker's source is ``classbeat.models.providers.RouteModels``: ``resolve_route`` and then
``provider_for_choice``, or with ``CLASSBEAT_MODEL_PROVIDER=fake`` the fake provider, bypassing
routes. No job builds a provider any other way (``tests/test_models_jobs_guard.py``). This module
imports no provider adapter: the web process imports the kinds, which name these types.
"""

from dataclasses import dataclass
from typing import Protocol

from classbeat.agent.loop import AgentConfig
from classbeat.agent.provider import ModelProvider
from classbeat.jobs.run import JobRun
from classbeat.models.store import StageGroup


@dataclass(frozen=True, slots=True)
class GroupModel:
    provider: ModelProvider
    agent: AgentConfig


class ModelSource(Protocol):
    """``(run, route_key, group, agent) -> GroupModel``: a failure that is the operator's (an
    unusable route, no key, a missing fake script) is ``JobFailure("model_config")``, raised
    before any model request."""

    async def __call__(
        self, run: JobRun, route_key: str, group: StageGroup, agent: AgentConfig
    ) -> GroupModel: ...
