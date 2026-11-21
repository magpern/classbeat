"""Phase 1 of a song pick (ADR 0008 sections 2 to 5): from the brief to a ``song_list`` revision and
the wait for the user's one approval. Everything runs in the worker, inside the job's lease:

1. ``pool_search``: the ``pick_pool`` stage (model; ``search_tracks`` through the job's Spotify
   connection, ``classbeat.songs.search``; ``check_pool``, ``submit_pool``). The model names
   candidates by registry ``ref`` only; the backend maps them to Spotify ids. For an owner the
   spotdown match is on for (off by default; SD-P6, #1303), ``classbeat.songs.pick_match`` then
   matches the pool with spotdown (metadata only, nothing downloaded), leaves out the genuinely
   missing candidates while the others can fill the list and otherwise runs bounded replacement
   rounds; spotdown unreachable or any failure of it ends the job (``outage_policy``): no fallback.
   After ``choose`` (step 4), ``classbeat.songs.pick_analyse`` analyses the chosen songs only
   and replaces an unsuitable one alone from the spares (SD-P6 stage B; no fallback either).
2. ``pool_write``: ``classbeat.playlists.write_pool`` (ensure the pool playlist: a
   ``spotify_create_playlist`` under this job's own key when the pool row has no playlist yet; the
   owner check; a ``spotify_replace_items`` of exactly the candidates, read back and compared),
   then the MOWL attach of the pool playlist (``mowl_attach_spotify_playlist``, key
   ``spotify:<pool id>``, once: later picks reuse the stored link).
3. ``pool_analyse``: the design music step on the pool playlist (``classbeat.design.music``), which
   first waits until MOWL's track list is exactly the candidates just written
   (``pool_not_synced`` after ``CLASSBEAT_SPOTIFY_POOL_SYNC_WAIT_SECONDS``; retried once), then
   analyses it; a candidate still without a BPM is left out and counted (``omitted_no_bpm``),
   and fewer usable tracks than songs to find is ``pool_too_small`` (not retried).
4. ``choose``: the ``choose_songs`` stage (model; ``get_pool``, ``check_song_list``,
   ``submit_song_list``). The list it returns is built from the analysis
   (``classbeat.agent.ChosenList``).
5. ``AwaitApproval``: in the final fenced transaction the Spotify connection is share-locked
   (still that generation and account, ``not_connected`` otherwise), the MOWL connection and the
   pool's link are checked again (``MusicStep.still_current``), and the ``song_list`` revision is
   stored with its hash (``classbeat.songs.song_list``); the runner then sets
   ``waiting_for_approval`` with ``approval_*`` and ``expires_at`` in the same transaction.

The pool analysis lives in memory for this run (ADR 0008 section 8); a retried phase 1 starts the
stages again, and the cost ceiling (``CLASSBEAT_SONG_JOB_COST_MICROS``) counts every attempt.

**A replace** (``seed``): the seed list's songs outside ``replace`` stay in their slots
(``KeptSong``: their stored ids, titles, roles, BPM and lengths); the replaced slots' roles are
what the pool must cover. The new list's parent is the seed.

**Never in a model's input**: tokens, the Spotify user id, playlist names or ids, track ids or
URIs, album cover URLs, MOWL ids (``classbeat.agent.model_input``; the stages see refs, indexes
and text only). A song's cover comes from the search hit its ref names (``SearchRegistry``) or,
for a kept song, from the seed list, never from the model (ADR 0013 section 9).
"""

import logging
from collections.abc import Sequence
from typing import Any, Literal
from uuid import UUID

from classbeat.agent import (
    AgentConfig,
    ChooseContext,
    ChosenList,
    JobStageHost,
    PickPlan,
    PoolContext,
    PoolResult,
    SearchRegistry,
    choose_stage,
    pool_stage,
    provenance,
    run_stage,
)
from classbeat.agent import choose_songs as choose_module
from classbeat.agent import pick_pool as pool_module
from classbeat.agent.brief import Brief
from classbeat.agent.limits import Limits
from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.provider import ModelProvider
from classbeat.agent.schemas import SONG_SCHEMA_VERSION
from classbeat.agent.songs import analysed_pool
from classbeat.agent.taste_check_policy import list_unmet
from classbeat.connections import OpenConnection, hold
from classbeat.crypto.keks import KeyRing
from classbeat.design.music import MusicStep, load_music
from classbeat.jobs import AwaitApproval, FencedSession, JobFailure, JobRun
from classbeat.operations import attach_spotify_playlist
from classbeat.playlists import open_spotify, write_pool
from classbeat.songs import song_list
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import SongJob
from classbeat.songs.pick_analyse import analyse_list, kept_timing
from classbeat.songs.pick_explicit import explicit_report
from classbeat.songs.pick_match import MatchedPool, match_pool
from classbeat.songs.search import job_searcher
from classbeat.songs.seed import SeedSpec, load_seeded
from classbeat.spotdown.song_analysis_record_model import timing_marks

logger = logging.getLogger(__name__)

Origin = Literal["design_with_songs", "revise_songs"]


def stage_config(agent: AgentConfig, config: SongConfig, *, turns: int, calls: int) -> AgentConfig:
    """The song stages' limits (ADR 0008 section 3): their own turns and tool calls, 3
    corrections, the agent's wall time and turn reserve, and the song kinds' job ceiling."""
    return AgentConfig(  # the model, effort and route stay the group's
        model=agent.model,
        effort=agent.effort,
        max_tokens=agent.max_tokens,
        limits=Limits(
            max_turns=turns,
            max_tool_calls=calls,
            max_corrections=3,
            stage_seconds=agent.limits.stage_seconds,
            job_cost_micros=config.cost_micros,
            turn_output_reserve=agent.limits.turn_output_reserve,
        ),
        prices=agent.prices,
        route_key=agent.route_key,
        eval_result_id=agent.eval_result_id,
    )


async def _pool(
    run: JobRun,
    provider: ModelProvider,
    plan: PickPlan,
    registry: SearchRegistry,
    searcher: Any,
    config: AgentConfig,
) -> tuple[PoolResult, Any]:
    ctx = PoolContext(plan, registry, searcher)
    outcome = await run_stage(
        JobStageHost(run, "pool_search"), provider, pool_stage(ctx), ctx.first_message(), config
    )
    if not isinstance(outcome.output, PoolResult) or not outcome.output.picks:
        raise JobFailure("internal")
    return outcome.output, outcome


async def write_pool_playlist(
    run: JobRun,
    keyring: KeyRing,
    spotify: OpenConnection,
    given: SongJob,
    track_ids: tuple[str, ...],
) -> str:
    await run.stage("pool_write")
    written = await write_pool(run, keyring, spotify, track_ids)
    await run.checkpoint()
    await attach_spotify_playlist(
        run, keyring, written.playlist_id, connection_id=given.mowl_connection_id
    )
    return written.playlist_id


async def run_phase_one(
    run: JobRun,
    given: SongJob,
    *,
    brief: Brief,
    seed: SeedSpec | None,
    origin: Origin,
    keyring: KeyRing,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
    extra_provenance: dict[str, Any] | None = None,
) -> AwaitApproval:
    if run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    spotify = await open_spotify(run, keyring, given.spotify_connection_id)
    account = spotify.account
    if account is None:  # open_spotify refuses it already
        raise JobFailure("not_connected")
    seeded = await load_seeded(run, seed)
    plan = (
        PickPlan.build(brief, replace=seeded.roles, kept=seeded.kept)
        if seeded is not None
        else PickPlan.build(brief)
    )
    pool_config = stage_config(
        agent, config, turns=pool_module.MAX_TURNS, calls=pool_module.MAX_TOOL_CALLS
    )
    registry, searcher = SearchRegistry(), job_searcher(run, keyring, spotify)
    pool, pool_outcome = await _pool(run, provider, plan, registry, searcher, pool_config)
    # SD-P6 (#1303, #1354): spotdown's match of the pool, always; a missing song is left out.
    matched = await match_pool(run, plan, pool, registry, (provider, searcher, pool_config))
    track_ids = tuple(p.spotify_id for p in matched.playlist)
    playlist_id = await write_pool_playlist(run, keyring, spotify, given, track_ids)

    await run.stage("pool_analyse")
    step = await load_music(
        run,
        keyring,
        playlist_id,
        connection_id=given.mowl_connection_id,
        expect=track_ids,
        sync_wait_seconds=config.sync_wait_seconds,
    )
    try:
        chosen, made = await _choose(
            run, provider, plan, matched, pool_outcome, step, agent, config, origin=origin
        )
        # SD-P6 (#1303): spotdown's analysis of the chosen songs only, and the refinement.
        chosen, analysis = await analyse_list(run, matched, step.data.analysis["map"], chosen)
        analysis = kept_timing(analysis, seeded, chosen)  # stage E: a kept song keeps its timing
        record = matched.record([s.spotify_id if s.new else "" for s in chosen.songs])
        flags, explicit = explicit_report(matched, chosen, analysis, seeded)  # report only, #1340
        built = song_list.build(
            chosen,
            account=account,
            provenance={**made, **record, **analysis, **flags, **(extra_provenance or {})},
            parent_id=seeded.revision_id if seeded is not None else None,
            generations=generations(spotify, step),
            unmet=list_unmet(plan.brief.music_taste, matched.eligible, chosen),  # issue #1133
            approximate=frozenset(p for p, _, _ in timing_marks(analysis)),  # "timing approximate"
            explicit=explicit,  # the "Explicit" tag
        )
    except BaseException:
        step.close()
        raise
    logger.info(
        "song list chosen",
        extra={
            "job_id": str(run.lease.job_id),
            "candidates": len(track_ids),
            "songs": len(built.content.songs),
            "omitted_no_bpm": built.content.omitted_no_bpm,
        },
    )

    return await_approval(spotify, step, built)


def await_approval(
    spotify: OpenConnection, step: MusicStep, built: song_list.SongListRevision
) -> AwaitApproval:
    """Step 5 (a pick, and a list from the user's own playlist, ``classbeat.songs.from_playlist``):
    the wait on the ``song_list`` revision ``built``, stored in the final fenced transaction."""

    async def store(fs: FencedSession) -> tuple[UUID, bytes]:
        """In the final fenced transaction: both connections still the ones the pick used, then
        the revision. A ``not_connected`` here rolls it back and ends the job with it."""
        if not await hold(fs.db, fs.lease.ctx, spotify):
            raise JobFailure("not_connected")
        await step.still_current(fs)
        revision_id = await built.store(fs)
        return revision_id, built.content_hash

    # The runner releases the step once the attempt is over, whether ``store`` ran or not (a
    # cancel, a lost lease or shutdown before it; #122 item 4).
    return AwaitApproval(store, release=step.close)


def pairs(picks: Sequence[PoolPick]) -> list[tuple[str, str]]:
    return [(p.spotify_id, p.role) for p in picks]


def generations(spotify: OpenConnection, step: MusicStep) -> song_list.Generations:
    mowl = step.generation
    if mowl is None:
        raise JobFailure("not_connected")
    return song_list.Generations(mowl=mowl, spotify=spotify.generation)


async def _choose(
    run: JobRun,
    provider: ModelProvider,
    plan: PickPlan,
    matched: MatchedPool,
    pool_outcome: Any,
    step: MusicStep,
    agent: AgentConfig,
    config: SongConfig,
    *,
    origin: Origin,
) -> tuple[ChosenList, dict[str, Any]]:
    analysis_map = step.data.analysis["map"]
    covers = {p.spotify_id: p.cover for p in matched.playlist}  # the search's, host-checked
    tracks, omitted = analysed_pool(analysis_map, pairs(matched.eligible), covers)
    ctx = ChooseContext.build(plan, tracks, omitted)  # pool_too_small
    choose_config = stage_config(
        agent, config, turns=choose_module.MAX_TURNS, calls=choose_module.MAX_TOOL_CALLS
    )
    outcome = await run_stage(
        JobStageHost(run, "choose"), provider, choose_stage(ctx), ctx.first_message(), choose_config
    )
    if not isinstance(outcome.output, ChosenList):
        raise JobFailure("internal")
    made = provenance(
        [pool_outcome, *matched.runs, outcome],
        analysis_map,
        origin=origin,
        schema_version=SONG_SCHEMA_VERSION,
    )
    return outcome.output, made.model_dump(mode="json")
