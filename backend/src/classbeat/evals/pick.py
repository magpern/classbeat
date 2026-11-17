"""The song-picking smoke cases (ADR 0008 section 3; issue #179): a pick, a replace and an injected
title, each run through the real ``pick_pool`` and ``choose_songs`` stages against one recorded
``FakeProvider`` script (the pool stage's turns, then the choose stage's), a synthetic Spotify
catalogue and a synthetic MOWL analysis. No Spotify, MOWL or model contact.

- ``PickCase``: the files of ``smoke_pick/``: the brief, the catalogue (invented tracks, some with
  odd titles or artists) and, for a replace case, the seed list (the roles of its slots) and which
  slots to replace. The kept songs of a replace case are invented from the seed.
- ``SyntheticCatalogue``: the ``Searcher`` the pool stage is given. A query matches a track when
  every word is in its title or artist; ``year:A-B`` in the query filters on the release year.
- ``analysis_map``: the MOWL analysis the choose stage reads, in the library's own format.
- ``run_pick_case``: both stages on an in-memory host, then deterministic checks
  (``classbeat.evals.pick_checks``).

These cases are not part of ``smoke --live`` (the live run is the design and revise cases): a pick
needs a Spotify search the owner's live run does not wire. They run in CI against ``FakeProvider``.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from classbeat.agent import run_stage
from classbeat.agent.choose_songs import ChooseContext, ChosenList, choose_stage
from classbeat.agent.limits import Limits
from classbeat.agent.loop import AgentConfig
from classbeat.agent.pick_pool import PoolContext, PoolResult, pool_stage
from classbeat.agent.provider import ModelProvider
from classbeat.agent.registry import SearchRegistry
from classbeat.agent.songs import (
    PoolTrack,
    analysed_pool,
)
from classbeat.evals.pick_case import (
    PickCase as PickCase,
)
from classbeat.evals.pick_case import (
    PickExpect as PickExpect,
)
from classbeat.evals.pick_case import (
    load_pick_cases as load_pick_cases,
)
from classbeat.evals.pick_case import (
    plan_for as plan_for,
)
from classbeat.evals.pick_checks import (  # re-exported: the checks live there (#1140)
    PickCheck as PickCheck,
)
from classbeat.evals.pick_checks import (
    case_bounds,
    ended_checks,
    limits_check,
    pick_checks,
)
from classbeat.evals.pick_checks import (
    shown as shown,
)
from classbeat.evals.pick_world import (
    CatalogueSpec as CatalogueSpec,
)
from classbeat.evals.pick_world import (
    CatalogueTrack as CatalogueTrack,
)
from classbeat.evals.pick_world import (
    ReplaceSpec as ReplaceSpec,
)
from classbeat.evals.pick_world import (
    SyntheticCatalogue as SyntheticCatalogue,
)
from classbeat.evals.pick_world import (
    analysis_map as analysis_map,
)
from classbeat.evals.runner import EvalHost
from classbeat.evals.song_checks import Band
from classbeat.jobs.errors import JobFailure


@dataclass
class PickCaseResult:
    name: str
    outcome: str  # "submitted" or the failure category
    checks: list[PickCheck] = field(default_factory=lambda: list[PickCheck]())
    pool: PoolResult | None = None
    chosen: ChosenList | None = None
    turns: int = 0
    tool_calls: int = 0
    cost_micros: int = 0
    prompt_versions: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    scores: dict[str, float] = field(default_factory=lambda: dict[str, float]())
    analysed: list[PoolTrack] = field(default_factory=lambda: list[PoolTrack]())
    expected_outcome: str = "submitted"  # the case's ``expect.outcome``

    @property
    def passed(self) -> bool:
        checked = bool(self.checks) and all(c.ok for c in self.checks)
        return self.outcome == self.expected_outcome and checked

    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]


def _with(config: AgentConfig, limits: Limits) -> AgentConfig:
    return AgentConfig(
        model=config.model,
        effort=config.effort,
        max_tokens=config.max_tokens,
        limits=limits,
        prices=config.prices,
    )


async def run_pick_case(
    case: PickCase,
    provider: ModelProvider,
    config: AgentConfig,
    limits: tuple[Limits, Limits],
    bands: Mapping[str, Band] | None = None,
) -> tuple[PickCaseResult, SyntheticCatalogue, SearchRegistry]:
    """Both stages of the case through ``run_stage`` on an in-memory host. ``limits`` are the
    pool stage's and the choose stage's (``Limits.for_song_stage``)."""
    catalogue = SyntheticCatalogue(case.catalogue)
    registry = SearchRegistry()
    plan = plan_for(case)
    result = PickCaseResult(case.name, "submitted", expected_outcome=case.expect.outcome)
    host = EvalHost()
    try:
        pool_ctx = PoolContext(plan, registry, catalogue.search)
        pooled = await run_stage(
            host,
            provider,
            pool_stage(pool_ctx),
            pool_ctx.first_message(),
            _with(config, limits[0]),
        )
        result.pool = pooled.output
        result.prompt_versions[pooled.info.stage] = pooled.info.prompt_version
        by_id = catalogue.by_id()
        wanted = [(p.spotify_id, p.role) for p in pooled.output.picks]
        tracks, omitted = analysed_pool(analysis_map([by_id[i] for i, _ in wanted]), wanted)
        result.analysed = tracks
        choose_ctx = ChooseContext.build(plan, tracks, omitted)
        chosen = await run_stage(
            host,
            provider,
            choose_stage(choose_ctx),
            choose_ctx.first_message(),
            _with(config, limits[1]),
        )
        result.chosen = chosen.output
        result.prompt_versions[chosen.info.stage] = chosen.info.prompt_version
        result.turns = pooled.turns + chosen.turns
        result.tool_calls = pooled.tool_calls + chosen.tool_calls
        result.cost_micros = pooled.cost_micros + chosen.cost_micros
    except JobFailure as failure:
        # What the run spent still counts against the budget, whatever stopped it (as run_case).
        c = host.counters
        result.outcome = failure.category
        result.turns, result.tool_calls, result.cost_micros = c.turns, c.tool_calls, c.cost_micros
        began = host.info.stage if host.info is not None else None
        result.checks = ended_checks(case, result, began)
        return result, catalogue, registry
    result.checks = pick_checks(case, plan, result, catalogue, sent_text(provider), bands)
    result.checks.append(limits_check(result, limits))
    if case.expect.max_turns is not None or case.expect.max_cost_micros is not None:
        result.checks.append(case_bounds(case, result))
    return result, catalogue, registry


def sent_text(provider: ModelProvider) -> str:
    """Everything a ``FakeProvider`` was given (the adapter's input), as one string."""
    parts: list[str] = []
    for sent in getattr(provider, "sent", []):
        spec = sent.spec
        parts += [spec.system, spec.first_message]
        parts += [json.dumps([t.name, t.description, dict(t.input_schema)]) for t in spec.tools]
        for results, reminder in sent.turns:
            parts += [r.content for r in results] + [reminder or ""]
    return "\n".join(parts)
