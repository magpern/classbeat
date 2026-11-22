"""spotdown's stages A and B for a list from the user's own Spotify playlist (ADR 0040 amendment
"spotdown is the only analysis path", items 3 to 5; #1386), in ``from_playlist``'s ``pool_analyse``
stage, after the list was built from MOWL's analysis of the playlist and before it is stored.

The list is the user's: nothing is chosen, so there are **no spares and no replacement rounds**.

1. **Stage A** (``pick_match_batch.match_picks``, the pick's batches, waits, keys and outage rule,
   inside the job's budget, ``pick_match_budget``): every distinct song of the list, by its title,
   artists and MOWL's length. A song that is not ``matched`` (``uncertain``, ``not_found``, a
   track's ``provider_error`` after its retry, ...) is **missing**, and a missing song of the
   user's list has no spare: ``analysis_failed`` (item 5).
2. **Stage B** (``pick_analyse.analyse_list``): every song is analysed as a ``fixed`` position
   (``pick_refine.Refine``): an analysis that is missing or not ``approximate`` is
   ``analysis_failed``; one that does not fit its slot is accepted with a warning. The list itself
   (order, ids, titles, roles) never changes.
3. The match and analysis records go into the list's provenance, as a pick's, so phase 2's stage D
   (``phase2_spotdown``) reads every song's analysis again; it never designs on MOWL's sections.

**Outcomes**: as the pick's stages (``spotdown.outage_policy``): spotdown unreachable, a provider
outage or the budget spent -> ``analysis_unavailable``; any other error is not swallowed. Only
counts and codes are logged: never a title, an id, a source or a key.
"""

import logging
from collections.abc import Mapping
from typing import Any

from classbeat.agent import ChosenList
from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.registry import Entry
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs import pick_match_budget as budget
from classbeat.songs.pick_analyse import analyse_list
from classbeat.songs.pick_match import MatchedPool
from classbeat.songs.pick_match_batch import Session, match_picks
from classbeat.spotdown import outage_policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.song_swap_calc import Candidate, usable

logger = logging.getLogger(__name__)


def _entries(chosen: ChosenList) -> tuple[list[PoolPick], dict[str, Entry]]:
    """One pick and one registry entry per distinct song (a song listed twice is matched once)."""
    picks: list[PoolPick] = []
    entries: dict[str, Entry] = {}
    for n, s in enumerate(chosen.songs):
        if s.spotify_id in entries:
            continue
        length = int(s.length_sec)
        entries[s.spotify_id] = Entry(n, s.title, s.artist, "", None, length, s.spotify_id)
        picks.append(PoolPick(n, s.role, s.spotify_id))
    return picks, entries


async def _matched(run: JobRun, chosen: ChosenList) -> MatchedPool:
    """Stage A: every song matched, or ``JobFailure``."""
    spent, unavailable = await budget.open(run)
    if unavailable:
        raise JobFailure(outage_policy.UNAVAILABLE)  # an earlier run of this attempt found it
    session = Session(run, SpotdownClient(), spent)
    picks, entries = _entries(chosen)
    matches = await match_picks(session, picks, entries, 0)
    candidates = {
        p.spotify_id: Candidate(
            p.spotify_id, p.role, entries[p.spotify_id].title, entries[p.spotify_id].artist,
            matches[p.spotify_id].use, matches[p.spotify_id].status,
            matches[p.spotify_id].confidence,
        )
        for p in picks
    }  # fmt: skip
    missing = sum(not usable(c) for c in candidates.values())
    if missing:
        raise JobFailure(outage_policy.FAILED)  # the user's song has no spare (item 5)
    return MatchedPool(eligible=tuple(picks), matches=matches, candidates=candidates)


async def playlist_spotdown(
    run: JobRun, chosen: ChosenList, analysis_map: Mapping[str, Any]
) -> dict[str, Any]:
    """The provenance records of stages A and B for the user's list (module docstring), or
    ``JobFailure``. The list is never changed: a fixed song is never replaced."""
    matched = await _matched(run, chosen)
    fixed = frozenset(range(len(chosen.songs)))
    refined, analysis = await analyse_list(run, matched, analysis_map, chosen, fixed)
    if refined.songs != chosen.songs:
        raise JobFailure(outage_policy.INTERNAL)  # a fixed song is never replaced
    logger.info(
        "spotdown own playlist",
        extra={"job_id": str(run.lease.job_id), "songs": len(chosen.songs)},
    )
    return {**matched.record([s.spotify_id for s in chosen.songs]), **analysis}
