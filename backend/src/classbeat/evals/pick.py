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
- ``run_pick_case``: both stages on an in-memory host, then deterministic checks.

These cases are not part of ``smoke --live`` (the live run is the design and revise cases): a pick
needs a Spotify search the owner's live run does not wire. They run in CI against ``FakeProvider``.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from classbeat.agent import run_stage
from classbeat.agent.choose_songs import ChooseContext, ChosenList, choose_stage
from classbeat.agent.limits import Limits
from classbeat.agent.loop import AgentConfig
from classbeat.agent.pick_pool import PoolContext, PoolResult, pool_stage
from classbeat.agent.provider import ModelProvider
from classbeat.agent.registry import MAX_TEXT, SearchRegistry
from classbeat.agent.schemas import Brief, clean_text
from classbeat.agent.songs import (
    PickPlan,
    PoolTrack,
    analysed_pool,
    total_sec,
    within_tolerance,
)
from classbeat.evals import song_checks
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
from classbeat.evals.pick_world import (
    kept_songs,
)
from classbeat.evals.runner import EvalHost
from classbeat.evals.song_checks import Band, PoolRow, SongFacts
from classbeat.jobs.errors import JobFailure


class PickExpect(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    forbidden_text: list[str] = Field(default_factory=lambda: list[str]())  # in the note
    years: list[int] | None = Field(default=None, min_length=2, max_length=2)  # the era asked for
    # The expected end of the run: a list (``submitted``) or the failure the case is about
    # (``pool_too_small``: too few usable tracks, nothing stored). The manifest lists the cases
    # that expect the latter (``Manifest.expected_outcomes``); the gate reads it from there.
    outcome: Literal["submitted", "pool_too_small"] = "submitted"
    max_turns: int | None = Field(default=None, ge=1)  # the case's own bounds (cost category)
    max_cost_micros: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _years_ordered(self) -> "PickExpect":
        if self.years is not None and self.years[0] > self.years[1]:
            raise ValueError("the era runs from the earlier year to the later")
        return self


class PickCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    about: str = Field(min_length=1, max_length=200)
    brief: Brief
    catalogue: CatalogueSpec
    replace: ReplaceSpec | None = None
    expect: PickExpect = Field(default_factory=PickExpect)
    # Run the song-quality checks of ADR 0012 section 4.1 (``song_checks``); they need the
    # manifest's band table, which the caller passes to ``run_pick_case``.
    quality: bool = False


def load_pick_cases() -> list[PickCase]:
    folder = resources.files("classbeat.evals") / "smoke_pick"
    cases = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if not item.name.endswith(".json"):
            continue
        case = PickCase.model_validate_json(item.read_text("utf-8"))
        if item.name != f"{case.name}.json":
            raise ValueError(f"{item.name}: the file is named after its case, {case.name}")
        cases.append(case)
    return cases


def plan_for(case: PickCase) -> PickPlan:
    if case.replace is None:
        return PickPlan.build(case.brief)
    spec = case.replace
    return PickPlan.build(
        case.brief,
        replace=[spec.seed_roles[s] for s in sorted(spec.replace)],
        kept=kept_songs(spec),
    )


@dataclass(frozen=True)
class PickCheck:
    name: str
    ok: bool
    detail: str = ""


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
        return (
            self.outcome == self.expected_outcome
            and bool(self.checks)
            and all(c.ok for c in self.checks)
        )

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
        result.checks = ended_checks(case, result)
        return result, catalogue, registry
    result.checks = pick_checks(case, plan, result, catalogue, sent_text(provider), bands)
    result.checks.append(limits_check(result, limits))
    if case.expect.max_turns is not None or case.expect.max_cost_micros is not None:
        result.checks.append(case_bounds(case, result))
    return result, catalogue, registry


def limits_check(result: PickCaseResult, limits: tuple[Limits, Limits]) -> PickCheck:
    """The suite's ``limits`` check of a song run (ADR 0012 section 4.3: every run passes it):
    both stages' turns and tool calls within their sum, the cost within the job ceiling."""
    turns = limits[0].max_turns + limits[1].max_turns
    calls = limits[0].max_tool_calls + limits[1].max_tool_calls
    ceiling = max(limits[0].job_cost_micros, limits[1].job_cost_micros)
    return PickCheck(
        "limits",
        result.turns <= turns and result.tool_calls <= calls and result.cost_micros <= ceiling,
        f"{result.turns} turns of {turns}, {result.tool_calls} tool calls of {calls}, "
        f"cost {result.cost_micros} of {ceiling} micro-dollars",
    )


def ended_checks(case: PickCase, result: PickCaseResult) -> list[PickCheck]:
    """The checks of a run that ended in a failure category: only a case that expects that
    category has any (it ended as expected and nothing was stored: no list came out of it)."""
    if case.expect.outcome != result.outcome:
        return []
    return [
        PickCheck("expected_outcome", True, result.outcome),
        PickCheck("nothing_stored", result.chosen is None),
    ]


def case_bounds(case: PickCase, result: PickCaseResult) -> PickCheck:
    """The cost category's per-case bounds (ADR 0012 section 4): turns and cost of the whole run."""
    turns, cost = case.expect.max_turns, case.expect.max_cost_micros
    return PickCheck(
        "case_bounds",
        (turns is None or result.turns <= turns) and (cost is None or result.cost_micros <= cost),
        f"{result.turns} turns of {turns}, cost {result.cost_micros} of {cost} micro-dollars",
    )


def shown(text: str) -> str:
    """A catalogue text as the registry shows it to the model: cleaned and cut."""
    return clean_text(text)[:MAX_TEXT].strip()


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


def pick_checks(
    case: PickCase,
    plan: PickPlan,
    result: PickCaseResult,
    catalogue: SyntheticCatalogue,
    sent: str,
    bands: Mapping[str, Band] | None = None,
) -> list[PickCheck]:
    chosen, pool = result.chosen, result.pool
    if chosen is None or pool is None:
        return [PickCheck("submitted", False, "no output")]
    by_id: Mapping[str, CatalogueTrack] = catalogue.by_id()
    kept = {k.spotify_id: k for k in plan.kept}
    songs = chosen.songs
    total = total_sec([s.length_sec for s in songs])
    checks = [
        PickCheck("total", within_tolerance(total, plan.target_sec), f"{round(total)} s"),
        PickCheck("no_repeat", len({s.spotify_id for s in songs}) == len(songs)),
        PickCheck(
            "ends",
            songs[0].role in ("warmup", "build") and songs[-1].role == "cooldown",
            f"{songs[0].role} to {songs[-1].role}",
        ),
        PickCheck(
            "from_analysis",
            all(
                (s.spotify_id in kept and s.title == kept[s.spotify_id].title)
                or (s.spotify_id in by_id and s.title == shown(by_id[s.spotify_id].title))
                for s in songs
            ),
        ),
        PickCheck(
            "pool_in_registry",
            all(by_id.get(p.spotify_id) is not None for p in pool.picks),
        ),
        PickCheck(
            "no_ids_sent",
            not any(
                i in sent
                for i in [*by_id, *kept, "spotify:track", "open.spotify.com", "api.spotify.com"]
            ),
        ),
        PickCheck(
            "forbidden_text",
            not any(f.lower() in chosen.note.lower() for f in case.expect.forbidden_text),
        ),
    ]
    if plan.replace:
        in_slot = all(
            0 <= k.slot < len(songs)
            and songs[k.slot].spotify_id == k.spotify_id
            and songs[k.slot].role == k.role
            and not songs[k.slot].new
            for k in plan.kept
        )
        checks.append(PickCheck("kept_in_place", in_slot))
        checks.append(PickCheck("replaced_are_new", sum(s.new for s in songs) == len(plan.replace)))
    if case.quality:
        if bands is None:
            raise ValueError("a quality case needs the manifest's band table")
        facts = SongFacts(
            pool=[
                PoolRow(p.role, by_id[p.spotify_id].title, by_id[p.spotify_id].artist,
                        by_id[p.spotify_id].year)
                for p in pool.picks
            ],
            queries=catalogue.queries,
            drops={t.spotify_id: len(t.drops) for t in result.analysed},
            years=(case.expect.years[0], case.expect.years[1]) if case.expect.years else None,
        )  # fmt: skip
        checks += [
            PickCheck(n, ok, d) for n, ok, d in song_checks.song_checks(plan, chosen, facts, bands)
        ]
        result.scores = song_checks.song_scores(plan, chosen, facts, bands)
    return checks
