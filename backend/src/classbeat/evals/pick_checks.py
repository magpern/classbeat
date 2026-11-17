"""The checks of a song case's run (ADR 0008 section 3, ADR 0012 section 4.1), split out of
``classbeat.evals.pick`` (#1140, CLAUDE.md rule 2), which runs a case and re-exports these names.

- ``pick_checks``: the list the stages accepted (the total, the order, no repeat, every song from
  the analysis and the pool from the registry, no Spotify id sent, the forbidden text, a replace's
  kept songs), the checks the case's ``expect`` asks for and, for a quality case, ``song_checks``.
  A brief with stated eras (``Brief.music_taste``) is also judged by the backend's own check of the
  chosen list (``taste_check_policy.list_unmet``): ``taste_eras`` fails on ``era_too_few`` or
  ``songs_outside_eras``;
- ``limits_check``, ``case_bounds``: the suite's limits and the cost category's bounds;
- ``ended_checks``: a run that ended in the failure category its case expects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from classbeat.agent.choose_songs import STAGE as CHOOSE_STAGE
from classbeat.agent.limits import Limits
from classbeat.agent.registry import MAX_TEXT
from classbeat.agent.schemas import clean_text
from classbeat.agent.songs import PickPlan, total_sec, within_tolerance
from classbeat.agent.taste_check_policy import ERA_TOO_FEW, OUTSIDE_ERAS, list_unmet
from classbeat.evals import song_checks
from classbeat.evals.pick_case import PickCase
from classbeat.evals.pick_world import CatalogueTrack, SyntheticCatalogue
from classbeat.evals.song_checks import Band, PoolRow, SongFacts

if TYPE_CHECKING:
    from classbeat.evals.pick import PickCaseResult


@dataclass(frozen=True)
class PickCheck:
    name: str
    ok: bool
    detail: str = ""


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


def ended_checks(case: PickCase, result: PickCaseResult, began: str | None) -> list[PickCheck]:
    """The checks of a run that ended in a failure category: only a case that expects that
    category has any (it ended as expected and nothing was stored). ``began`` is the stage that
    began last on the run's host. ``nothing_stored`` fails when a list came out or the choose stage
    began at all: a pool too small to choose from must end before any model turn of the choose
    stage (#219; before it, the check could not fail, because ``chosen`` stays ``None`` on every
    failure path)."""
    if case.expect.outcome != result.outcome:
        return []
    started = began == CHOOSE_STAGE
    return [
        PickCheck("expected_outcome", True, result.outcome),
        PickCheck(
            "nothing_stored",
            result.chosen is None and not started,
            "the choose stage began" if started else "no list, no choose stage",
        ),
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
    taste = plan.brief.music_taste
    if taste is not None and taste.eras:  # #1140: the production check of the chosen list
        misses = [m.reason_code for m in list_unmet(taste, pool.picks, chosen)]
        eras = [m for m in misses if m in (ERA_TOO_FEW, OUTSIDE_ERAS)]
        checks.append(PickCheck("taste_eras", not eras, ", ".join(eras) or "every era kept"))
    rows = song_checks.expected_rows(case.expect, songs, result.analysed, sent)
    checks += [PickCheck(*r) for r in rows]
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
