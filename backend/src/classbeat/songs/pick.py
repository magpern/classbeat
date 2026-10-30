"""Phase 1 of a song pick (ADR 0008 sections 2 to 5): from the brief to a ``song_list`` revision and
the wait for the user's one approval. Everything runs in the worker, inside the job's lease:

1. ``pool_search``: the ``pick_pool`` stage (model; ``search_tracks`` through the job's Spotify
   connection, ``classbeat.songs.search``; ``check_pool``, ``submit_pool``). The model names
   candidates by registry ``ref`` only; the backend maps them to Spotify ids.
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
URIs, MOWL ids (``classbeat.agent.model_input``; the stages see refs, indexes and text only).
"""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text

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
from classbeat.agent.limits import Limits
from classbeat.agent.provider import ModelProvider
from classbeat.agent.schemas import SONG_SCHEMA_VERSION
from classbeat.agent.songs import KeptSong, analysed_pool
from classbeat.connections import OpenConnection, hold
from classbeat.crypto.keks import KeyRing
from classbeat.design.music import MusicStep, load_music
from classbeat.jobs import AwaitApproval, FencedSession, JobFailure, JobRun
from classbeat.operations import attach_spotify_playlist
from classbeat.playlists import open_spotify, write_pool
from classbeat.songs import admit, song_list
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.search import job_searcher

logger = logging.getLogger(__name__)

ORIGIN = "design_with_songs"


@dataclass(frozen=True)
class Seeded:
    """A replace: the plan's kept songs and replaced roles, and the seed list (the parent)."""

    revision_id: UUID
    kept: tuple[KeptSong, ...]
    roles: tuple[str, ...]


def stage_config(agent: AgentConfig, config: SongConfig, *, turns: int, calls: int) -> AgentConfig:
    """The song stages' limits (ADR 0008 section 3): their own turns and tool calls, 3
    corrections, the agent's wall time and turn reserve, and the song kinds' job ceiling."""
    limits = Limits(
        max_turns=turns,
        max_tool_calls=calls,
        max_corrections=3,
        stage_seconds=agent.limits.stage_seconds,
        job_cost_micros=config.cost_micros,
        turn_output_reserve=agent.limits.turn_output_reserve,
    )
    return AgentConfig(
        model=agent.model,
        effort=agent.effort,
        max_tokens=agent.max_tokens,
        limits=limits,
        prices=agent.prices,
    )


async def _seeded(run: JobRun, given: DesignWithSongsInput) -> Seeded | None:
    seed = given.seed
    if seed is None:
        return None
    async with run.fenced() as fs:
        class_id = await _class_of(fs)
        found = await admit.seed_list(fs.db, run.lease.owner_id, class_id, seed, newest=False)
    if found is None:
        raise JobFailure("invalid_input")
    replaced = set(seed.replace)
    kept = tuple(
        KeptSong(slot, s.spotify_id, s.title, s.artist, s.role, s.bpm, s.length_sec)
        for slot, s in enumerate(found.content.songs)
        if slot not in replaced
    )
    roles = tuple(found.content.songs[slot].role for slot in sorted(replaced))
    return Seeded(seed.song_list_revision_id, kept, roles)


_JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def _class_of(fs: FencedSession) -> UUID:
    """The job's own class, from the fenced job row (never from the input)."""
    row = (await fs.db.execute(_JOB_CLASS, fs.lease.params())).first()
    if row is None:
        raise JobFailure("internal")
    return row.class_id


async def _pool(
    run: JobRun, provider: ModelProvider, plan: PickPlan, searcher: Any, config: AgentConfig
) -> tuple[PoolResult, Any]:
    ctx = PoolContext(plan, SearchRegistry(), searcher)
    outcome = await run_stage(
        JobStageHost(run, "pool_search"), provider, pool_stage(ctx), ctx.first_message(), config
    )
    if not isinstance(outcome.output, PoolResult) or not outcome.output.picks:
        raise JobFailure("internal")
    return outcome.output, outcome


async def _write(
    run: JobRun,
    keyring: KeyRing,
    spotify: OpenConnection,
    given: DesignWithSongsInput,
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
    given: DesignWithSongsInput,
    *,
    keyring: KeyRing,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
) -> AwaitApproval:
    if run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    spotify = await open_spotify(run, keyring, given.spotify_connection_id)
    account = spotify.account
    if account is None:  # open_spotify refuses it already
        raise JobFailure("not_connected")
    seeded = await _seeded(run, given)
    plan = (
        PickPlan.build(given.brief, replace=seeded.roles, kept=seeded.kept)
        if seeded is not None
        else PickPlan.build(given.brief)
    )
    pool_config = stage_config(
        agent, config, turns=pool_module.MAX_TURNS, calls=pool_module.MAX_TOOL_CALLS
    )
    pool, pool_outcome = await _pool(
        run, provider, plan, job_searcher(run, keyring, spotify), pool_config
    )
    track_ids = tuple(p.spotify_id for p in pool.picks)
    playlist_id = await _write(run, keyring, spotify, given, track_ids)

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
        stored = await _choose(run, provider, plan, pool, pool_outcome, step, agent, config)
        built = song_list.build(
            stored[0],
            account=account,
            provenance=stored[1],
            parent_id=seeded.revision_id if seeded is not None else None,
            generations=_generations(spotify, step),
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

    async def store(fs: FencedSession) -> tuple[UUID, bytes]:
        """In the final fenced transaction: both connections still the ones the pick used, then
        the revision. A ``not_connected`` here rolls it back and ends the job with it."""
        try:
            if not await hold(fs.db, fs.lease.ctx, spotify):
                raise JobFailure("not_connected")
            await step.still_current(fs)
            revision_id = await built.store(fs)
            return revision_id, built.content_hash
        finally:
            step.close()

    # TODO(#234): pass ``release=step.close`` once the runner's ``release`` hook is merged; until
    # then the step is closed only inside ``store`` (an attempt that never stores keeps it open).
    return AwaitApproval(store)


def _generations(spotify: OpenConnection, step: MusicStep) -> song_list.Generations:
    mowl = step.generation
    if mowl is None:
        raise JobFailure("not_connected")
    return song_list.Generations(mowl=mowl, spotify=spotify.generation)


async def _choose(
    run: JobRun,
    provider: ModelProvider,
    plan: PickPlan,
    pool: PoolResult,
    pool_outcome: Any,
    step: MusicStep,
    agent: AgentConfig,
    config: SongConfig,
) -> tuple[ChosenList, dict[str, Any]]:
    analysis_map = step.data.analysis["map"]
    wanted = [(p.spotify_id, p.role) for p in pool.picks]
    tracks, omitted = analysed_pool(analysis_map, wanted)
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
        [pool_outcome, outcome],
        analysis_map,
        origin=ORIGIN,
        schema_version=SONG_SCHEMA_VERSION,
    )
    return outcome.output, made.model_dump(mode="json")
