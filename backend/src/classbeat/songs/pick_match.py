"""The spotdown match stage of a Spotify song pick (ADR 0040 amendment "spotdown is the only
analysis path", plan sections 1 and 6; SD-P6 stage A, #1303, #1354), between the ``pick_pool``
stage and the pool write, inside the ``pool_search`` stage. **Nothing is downloaded or analysed**:
``POST /v2/match`` gets each candidate's metadata (title, artists, duration; ``client_ref`` an index
of the batch, never a Spotify id) and answers a status, a recording-match confidence and up to 2
alternates. It always runs: there is no switch and no fallback to MOWL's sections.

1. The job's budget row (``pick_match_budget``): an earlier run of this attempt that found spotdown
   unavailable ends ``analysis_unavailable`` at once.
2. ``/v2/match`` per batch of at most 50 (``pick_match_batch``: the retry, the ``rate_limited``
   waits inside the attempt's budget, the ``Idempotency-Key``s, the outage rule).
3. Spares first, then replacement rounds (``song_swap_calc``): while the usable candidates cannot
   fill the list, the song agent (a ``pick_pool`` stage for the missing roles) finds new ones,
   which are matched again; at most ``MAX_ROUNDS`` rounds; each swap is recorded.
4. The pool playlist then holds the usable candidates only (``MatchedPool.eligible``); the missing
   ones are left out. Still too few after the rounds: ``analysis_failed``.

**Outcomes** (item 4 of the amendment): spotdown unreachable, a provider outage, a wait that does
not fit the budget -> ``analysis_unavailable`` (``pick_unavailable``); any other error is
``internal`` and is not swallowed (a ``ValueError`` in the record included). Only counts and codes
are logged: never a title, a source, a ref or a key.

**The seam of the later stages** (analysis, refinement): ``MatchedPool.matches`` (a candidate's
``TrackMatch``, its sources in memory), ``MatchedPool.spares`` (the ranked spares of a slot) and
the job's budget (``pick_match_budget``).
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from classbeat.agent import (
    AgentConfig,
    JobStageHost,
    PickPlan,
    PoolContext,
    PoolResult,
    SearchRegistry,
    StageOutcome,
    pool_stage,
    run_stage,
)
from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.provider import ModelProvider
from classbeat.agent.registry import Entry
from classbeat.agent.schemas import MAX_POOL, MAX_SONGS
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs import pick_match_budget as budget
from classbeat.songs.pick_match_batch import Session, match_picks
from classbeat.spotdown import outage_policy, song_match_config
from classbeat.spotdown import song_match_policy as policy
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.song_match_policy import TrackMatch
from classbeat.spotdown.song_match_record_model import match_record
from classbeat.spotdown.song_swap_calc import (
    Candidate,
    Swap,
    pair_swaps,
    ranked_spares,
    short,
    to_replace,
    usable,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchedPool:
    """What the pick goes on with."""

    eligible: tuple[PoolPick, ...]  # the pool playlist's tracks and the choose stage's candidates
    matches: Mapping[str, TrackMatch] = field(default_factory=lambda: {}, repr=False)
    candidates: Mapping[str, Candidate] = field(default_factory=lambda: {}, repr=False)
    swaps: Mapping[str, Swap] = field(default_factory=lambda: {}, repr=False)
    rounds: int = 0
    missing: int = 0  # the pool's candidates that were left out
    runs: tuple[StageOutcome, ...] = ()  # the replacement rounds' stage runs

    @property
    def playlist(self) -> tuple[PoolPick, ...]:
        return self.eligible

    def explicit_of(self, spotify_id: str) -> bool | None:
        """Spotify's explicit flag of a candidate (report only, #1340); ``None``: unknown."""
        return next((p.explicit for p in self.playlist if p.spotify_id == spotify_id), None)

    def spares(self, role: str, listed: Sequence[str]) -> list[PoolPick]:
        """The ranked spares of a slot of ``role`` (``listed``: the list's Spotify ids)."""
        by_id = {p.spotify_id: p for p in self.eligible}
        found = [self.candidates[p.spotify_id] for p in self.eligible]
        return [by_id[c.spotify_id] for c in ranked_spares(found, set(listed), role)]

    def record(self, listed: Sequence[str]) -> dict[str, Any]:
        """The provenance record (``song_match_record_model``). An error in it is not swallowed."""
        return match_record(
            listed,
            self.matches,
            self.swaps,
            rounds=self.rounds,
            candidates=len(self.matches),
            missing=self.missing,
        )


def _candidate(pick: PoolPick, entry: Entry, match: TrackMatch) -> Candidate:
    return Candidate(
        pick.spotify_id, pick.role, entry.title, entry.artist, match.use, match.status,
        match.confidence,
    )  # fmt: skip


@dataclass
class _Rounds:
    """The replacement rounds' state."""

    found: list[Candidate]
    missing: list[Candidate]
    seen: set[str]
    fresh: list[PoolPick] = field(default_factory=lambda: [])
    swaps: dict[str, Swap] = field(default_factory=lambda: {})
    rounds: int = 0


async def _round(
    session: Session,
    state: _Rounds,
    plan: PickPlan,
    stage: tuple[ModelProvider, Any, AgentConfig],
    matches: dict[str, TrackMatch],
    entries: dict[str, Entry],
    runs: list[StageOutcome],
) -> None:
    """One replacement round: the song agent's new candidates for the missing ones it needs."""
    provider, searcher, config = stage
    state.rounds += 1
    wanted = to_replace(state.missing, state.found, plan.slots, plan.required_roles())[:MAX_SONGS]
    if not wanted:
        return
    ctx = PoolContext(
        PickPlan.build(plan.brief, replace=[c.role for c in wanted]), SearchRegistry(), searcher
    )
    host = JobStageHost(session.run, "pool_search")
    outcome = await run_stage(host, provider, pool_stage(ctx), ctx.first_message(), config)
    runs.append(outcome)
    if not isinstance(outcome.output, PoolResult):
        return
    new = [p for p in outcome.output.picks if p.spotify_id not in state.seen]
    state.seen |= {p.spotify_id for p in outcome.output.picks}
    if not new:
        return
    found_now = {e.spotify_id: e for e in ctx.registry.entries()}
    matched = await match_picks(session, new, found_now, state.rounds)
    matches |= matched
    entries |= found_now
    made = [_candidate(p, found_now[p.spotify_id], matched[p.spotify_id]) for p in new]
    paired, left = pair_swaps(wanted, made, state.rounds)
    state.missing = left + [c for c in state.missing if c not in wanted]
    state.swaps |= paired
    state.found += [c for c in made if usable(c)]
    state.fresh += [p for p, c in zip(new, made, strict=True) if usable(c)]


async def _match(
    run: JobRun,
    plan: PickPlan,
    pool: PoolResult,
    registry: SearchRegistry,
    stage: tuple[ModelProvider, Any, AgentConfig],
    runs: list[StageOutcome],
) -> MatchedPool:
    spent, unavailable = await budget.open(run)
    if unavailable:
        raise JobFailure(outage_policy.UNAVAILABLE)  # an earlier run of this attempt found it
    session = Session(run, SpotdownClient(song_match_config.current().sources), spent)
    entries = {e.spotify_id: e for e in registry.entries()}
    matches = await match_picks(session, pool.picks, entries, 0)
    made = [_candidate(p, entries[p.spotify_id], matches[p.spotify_id]) for p in pool.picks]
    state = _Rounds(
        [c for c in made if usable(c)],
        [c for c in made if not usable(c)],
        {p.spotify_id for p in pool.picks} | {k.spotify_id for k in plan.kept},
    )
    while (
        state.missing
        and state.rounds < policy.MAX_ROUNDS
        and short(state.found, plan.slots, plan.required_roles())
    ):
        await _round(session, state, plan, stage, matches, entries, runs)
    if state.missing and short(state.found, plan.slots, plan.required_roles()):
        raise JobFailure(outage_policy.FAILED)  # too few usable songs after the spares and rounds
    missing_ids = {c.spotify_id for c in made if not usable(c)}
    eligible = [p for p in pool.picks if p.spotify_id not in missing_ids] + state.fresh
    candidates = {c.spotify_id: c for c in [*made, *state.found]}
    return MatchedPool(
        eligible=tuple(eligible[:MAX_POOL]),
        matches=matches,
        candidates=candidates,
        swaps=state.swaps,
        rounds=state.rounds,
        missing=len(missing_ids),
        runs=tuple(runs),
    )


async def match_pool(
    run: JobRun,
    plan: PickPlan,
    pool: PoolResult,
    registry: SearchRegistry,
    stage: tuple[ModelProvider, Any, AgentConfig],
) -> MatchedPool:
    """The pool the pick goes on with (``stage``: the song agent's provider, searcher and
    limits, for a replacement round). ``JobFailure`` for every ending but a usable pool."""
    runs: list[StageOutcome] = []
    matched = await _match(run, plan, pool, registry, stage, runs)
    logger.info(
        "spotdown song match",
        extra={
            "job_id": str(run.lease.job_id),
            "candidates": len(matched.matches),
            "missing": matched.missing,
            "rounds": matched.rounds,
        },
    )
    return matched
