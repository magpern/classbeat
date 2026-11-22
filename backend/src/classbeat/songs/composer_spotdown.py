"""spotdown's stages A and B in the composer's ``fill`` and ``swap`` (ADR 0040 amendment "spotdown
is the only analysis path", items 3 to 5; ADR 0038 sections 5 and 6; #1386), around
``composer_pick``'s choice, exactly as a pick runs them (``pick``):

1. **Stage A** (``pick_match.match_pool``), after ``candidate_set`` and before the pool write: the
   one candidate set is matched; a missing candidate is left out while the others can fill the
   list, else the song agent's replacement rounds, else ``analysis_failed``. A song a swap takes
   out is never added by a round (``exclude``), so it is never written, analysed or offered again
   (#1188 review F1). A **pinned** song that is missing is not in the pool any more, so the choice
   makes it Not as asked ``pinned_not_found``, as for a pinned song MOWL could not analyse.
2. **Stage B** (``pick_analyse.analyse_list``), after the choice and the favoured rule: every new
   song is analysed and refined (replaced alone by a spare when missing or unsuitable); a pinned
   song is analysed as a ``fixed`` position, never replaced (missing: ``analysis_failed``;
   unsuitable: accepted with a warning). A swap's kept songs carry their waiting list's entries
   (``pick_analyse.kept_timing``, in ``composer``).
3. ``records``: the match and analysis records for the list's provenance, so the preview's design
   (``composer_design.songs_music``) and phase 2 (``phase2_spotdown``) read every song's analysis
   again: no composer design is timed on MOWL's sections.

**Outcomes** are the pick's (``spotdown.outage_policy``); nothing is swallowed. Only counts and
codes are logged: never a title, an id, a source or a key.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from classbeat.agent import AgentConfig, ChosenList, PickPlan, PoolResult, SearchRegistry
from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.provider import ModelProvider
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs.pick_analyse import analyse_list
from classbeat.songs.pick_match import MatchedPool, match_pool
from classbeat.spotdown import outage_policy


async def matched_candidates(
    run: JobRun,
    plan: PickPlan,
    picks: Sequence[PoolPick],
    registry: SearchRegistry,
    stage: tuple[ModelProvider, Any, AgentConfig],
    excluded: frozenset[str],
) -> MatchedPool:
    """Stage A on the candidate set (module docstring, step 1); ``JobFailure`` otherwise."""
    matched = await match_pool(run, plan, PoolResult(tuple(picks)), registry, stage, excluded)
    if any(p.spotify_id in excluded for p in matched.eligible):
        raise JobFailure(outage_policy.INTERNAL)  # a swapped-out song is never a candidate
    return matched


async def analysed_choice(
    run: JobRun,
    matched: MatchedPool,
    analysis_map: Mapping[str, Any],
    chosen: ChosenList,
    pins: frozenset[int],
) -> tuple[ChosenList, dict[str, Any]]:
    """Stage B on the chosen list, ``pins`` its pinned positions (module docstring, step 2): the
    refined list and the records of both stages (step 3)."""
    refined, analysis = await analyse_list(run, matched, analysis_map, chosen, pins)
    if any(refined.songs[p] != chosen.songs[p] for p in pins):
        raise JobFailure(outage_policy.INTERNAL)  # a pinned song is never replaced
    listed = [s.spotify_id if s.new or n in pins else "" for n, s in enumerate(refined.songs)]
    return refined, {**matched.record(listed), **analysis}
