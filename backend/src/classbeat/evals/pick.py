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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Final, Literal

from mowl import spotify
from pydantic import BaseModel, ConfigDict, Field

from classbeat.agent import run_stage
from classbeat.agent.choose_songs import ChooseContext, ChosenList, choose_stage
from classbeat.agent.limits import Limits
from classbeat.agent.loop import AgentConfig
from classbeat.agent.pick_pool import PoolContext, PoolResult, pool_stage
from classbeat.agent.provider import ModelProvider
from classbeat.agent.registry import MAX_TEXT, SearchRegistry
from classbeat.agent.schemas import Brief, clean_text
from classbeat.agent.songs import KeptSong, PickPlan, analysed_pool, total_sec, within_tolerance
from classbeat.evals.runner import EvalHost
from classbeat.jobs.errors import JobFailure

TRACK_SEC: Final = 240
FIRST_BPM: Final = 100
BPM_STEP: Final = 3
SECTION_SEC: Final = 60
LOUDNESS: Final = (-12.0, -6.0, -11.0, -5.0)
CATALOGUE_ID: Final = "SmokePick{:013d}"  # 22 characters
KEPT_ID: Final = "SmokeKept{:013d}"
YEAR_FILTER: Final = re.compile(r"\byear:(\d{4})(?:-(\d{4}))?\b")


Malformed = Literal["no_title", "no_artist", "bad_id", "title_not_text"]


class CatalogueSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    size: int = Field(ge=10, le=120)
    titles: dict[str, str] = Field(default_factory=lambda: dict[str, str]())  # index -> title
    artists: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    albums: dict[str, str] = Field(default_factory=lambda: dict[str, str]())
    no_bpm: list[int] = Field(default_factory=lambda: list[int]())  # MOWL has no BPM yet
    # Tracks whose search hit is malformed (see ``MALFORMED_HITS``): the hit the real tool-result
    # handling (``SearchRegistry.add_hits``) must skip, not the stub's.
    malformed: dict[str, Malformed] = Field(default_factory=lambda: dict[str, Malformed]())


class ReplaceSpec(BaseModel):
    """The seed list's slot roles and the slots to replace; the other slots' songs are kept."""

    model_config = ConfigDict(extra="forbid", strict=True)

    seed_roles: list[str] = Field(min_length=2, max_length=40)
    replace: list[int] = Field(min_length=1)


class PickExpect(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    forbidden_text: list[str] = Field(default_factory=lambda: list[str]())  # in the note
    # The expected end of the run: a list (``submitted``) or the failure the case is about
    # (``pool_too_small``: too few usable tracks, nothing stored). The manifest lists the cases
    # that expect the latter (``Manifest.expected_outcomes``); the gate reads it from there.
    outcome: Literal["submitted", "pool_too_small"] = "submitted"
    max_turns: int | None = Field(default=None, ge=1)  # the case's own bounds (cost category)
    max_cost_micros: int | None = Field(default=None, ge=0)


class PickCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    about: str = Field(min_length=1, max_length=200)
    brief: Brief
    catalogue: CatalogueSpec
    replace: ReplaceSpec | None = None
    expect: PickExpect = Field(default_factory=PickExpect)


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


@dataclass(frozen=True)
class CatalogueTrack:
    spotify_id: str
    title: str
    artist: str
    year: int
    sec: int
    bpm: int  # 0: MOWL has none
    group: str  # a hidden tag, ``group0`` for tracks 0 to 9: a query for it finds that slice
    album: str = "Invented Album"
    malformed: Malformed | None = None  # the hit is returned broken (see ``hit``)

    def hit(self) -> dict[str, Any]:
        """The library's track dict for this track; a ``malformed`` one is broken the way a
        search answer can be (no title, no artist, an id that is no Spotify id, a title that is
        not text), which ``SearchRegistry`` must skip."""
        out: dict[str, Any] = {
            "uri": f"spotify:track:{self.spotify_id}",
            "id": self.spotify_id,
            "title": self.title,
            "artists": [self.artist],
            "album": self.album,
            "year": str(self.year),
            "sec": self.sec,
            "duration_ms": self.sec * 1000,
            "release_date": f"{self.year}-01-01",
        }
        if self.malformed == "no_title":
            del out["title"]
        elif self.malformed == "no_artist":
            out["artists"] = []
        elif self.malformed == "bad_id":
            out["id"] = out["uri"] = "not an id"
        elif self.malformed == "title_not_text":
            out["title"] = {"text": self.title}
        return out


class SyntheticCatalogue:
    """Invented tracks: ``Smoke Song N`` by ``Smoke Artist N // 10``, 240 s, years 2000 to 2024."""

    def __init__(self, spec: CatalogueSpec) -> None:
        self.tracks = [
            CatalogueTrack(
                CATALOGUE_ID.format(i),
                spec.titles.get(str(i), f"Smoke Song {i}"),
                spec.artists.get(str(i), f"Smoke Artist {i // 10}"),
                2000 + i % 25,
                TRACK_SEC,
                0 if i in spec.no_bpm else FIRST_BPM + BPM_STEP * (i % 40),
                f"group{i // 10}",
                spec.albums.get(str(i), "Invented Album"),
                spec.malformed.get(str(i)),
            )
            for i in range(spec.size)
        ]
        self.queries: list[str] = []

    def search(self, query: str) -> list[dict[str, Any]]:
        """The ``Searcher``: the library's track dicts for at most ten matches."""
        self.queries.append(query)
        years = YEAR_FILTER.search(query)
        words = YEAR_FILTER.sub(" ", query).lower().split()
        out = []
        for t in self.tracks:
            if years and not int(years[1]) <= t.year <= int(years[2] or years[1]):
                continue
            if all(w in f"{t.title} {t.artist} {t.group}".lower() for w in words):
                out.append(t.hit())
        return out[:10]

    def by_id(self) -> dict[str, CatalogueTrack]:
        return {t.spotify_id: t for t in self.tracks}


def analysis_map(tracks: Sequence[CatalogueTrack]) -> dict[str, Any]:
    """The MOWL analysis of a playlist of ``tracks`` in play order (the library's format)."""
    rows: list[dict[str, Any]] = []
    sections: dict[str, list[dict[str, float]]] = {}
    for t in tracks:
        rows.append(
            {
                "Title": t.title,
                "Artist": t.artist,
                "Duration": t.sec * 1000,
                "BPM": t.bpm,
                "SpotifyTrackID": t.spotify_id,
            }
        )
        sections[t.spotify_id] = [
            {
                "start": float(s),
                "duration": float(min(SECTION_SEC, t.sec - s)),
                "loudness": LOUDNESS[n % len(LOUDNESS)],
            }
            for n, s in enumerate(range(0, t.sec, SECTION_SEC))
        ]
    return spotify.build_map(rows, sections)


def kept_songs(spec: ReplaceSpec) -> list[KeptSong]:
    """The seed list's songs that stay: invented, in their slots, with their roles."""
    return [
        KeptSong(
            slot,
            KEPT_ID.format(slot),
            f"Kept Song {slot}",
            f"Kept Artist {slot % 3}",
            role,
            float(FIRST_BPM + BPM_STEP * slot),
            TRACK_SEC,
        )
        for slot, role in enumerate(spec.seed_roles)
        if slot not in spec.replace
    ]


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
        result.outcome = failure.category
        result.checks = ended_checks(case, result)
        return result, catalogue, registry
    result.checks = pick_checks(case, plan, result, catalogue, sent_text(provider))
    return result, catalogue, registry


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


def shown(text: str) -> str:
    """A catalogue text as the registry shows it to the model: cleaned and cut."""
    return clean_text(text)[:MAX_TEXT].strip()


def pick_checks(
    case: PickCase,
    plan: PickPlan,
    result: PickCaseResult,
    catalogue: SyntheticCatalogue,
    sent: str,
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
    if case.expect.max_turns is not None or case.expect.max_cost_micros is not None:
        checks.append(case_bounds(case, result))
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
    return checks
