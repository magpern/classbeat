"""Phase 1's pool and choice for the composer's ``fill`` and ``swap`` (ADR 0038 sections 5 and 6,
#1188), on the stages of a pick (``agent.pick_pool``, ``agent.choose_songs``), unchanged:

1. **pool_search**: the ``pick_pool`` stage, with a registry that already holds the attached
   playlists' tracks (favoured) and the verified pinned songs (``composer_sources``); its first
   message adds, as facts, the sketch's song slots and which refs are favoured and pinned. After
   the stage the backend adds every pinned and favoured track the model left out (at most
   ``MAX_POOL`` in all; model picks are dropped from the end first): a favoured or pinned song is
   always a candidate, whatever the model did.
2. **pool_write** and **pool_analyse**: as a pick's (``pick``'s ``_write`` and the music step).
3. **choose**: the ``choose_songs`` stage. A fill with pinned songs is a replace-mode choice: the
   pinned songs, analysed, are kept at their slots (``composer_slots_policy.pin_slots``) and the
   other slots are filled from the pool; a pinned song MOWL could not analyse is Not as asked
   ``pinned_not_found``. A swap is the replace of the waiting list's slots (``seed``). Then the
   favoured tracks are preferred where they fit (``favoured_policy``).

Nothing here stores anything: the caller designs on the chosen songs and stores the list.
"""

import json
from dataclasses import dataclass
from typing import Any

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
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.pick_pool import PoolPick
from classbeat.agent.provider import ModelProvider
from classbeat.agent.schemas import MAX_POOL, SONG_SCHEMA_VERSION
from classbeat.agent.songs import KeptSong, analysed_pool
from classbeat.agent.taste_check_policy import list_unmet
from classbeat.connections import OpenConnection
from classbeat.crypto.keks import KeyRing
from classbeat.design.music import MusicStep, load_music
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs.composer_sources import PINNED_NOT_FOUND, Favoured, Pinned
from classbeat.songs.config import SongConfig
from classbeat.songs.favoured_policy import prefer_favoured
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.pick import stage_config, write_pool_playlist
from classbeat.songs.search import job_searcher
from classbeat.songs.song_list import MAX_UNMET

FAVOURED_ROLE = "build"  # a hint only: the choice gives every song its role


@dataclass(frozen=True)
class Picked:
    chosen: ChosenList
    provenance: dict[str, Any]
    unmet: tuple[InterpretationItem, ...]


def _message(ctx: PoolContext, slots: list[str], favoured: Favoured, pinned: Pinned) -> str:
    facts = {
        "sketch_slots": slots,
        "favoured_refs": [e.ref for e in favoured.entries],
        "pinned_refs": [e.ref for _, e in pinned.found],
    }
    return (
        ctx.first_message()
        + "\n\nThe class is already shaped; each slot is one song. Refs in favoured_refs come from"
        " the user's own playlists: put those that fit in the pool first. Refs in pinned_refs"
        " are songs the user named; they are in the class already. "
        + json.dumps(facts, separators=(",", ":"))
    )


def _with_extras(
    pool: PoolResult, favoured: Favoured, pinned: Pinned, pin_roles: dict[int, str]
) -> tuple[PoolPick, ...]:
    """The model's picks, in its order, then every pinned and favoured track it left out (module
    docstring, step 1), at most ``MAX_POOL``: the model's last picks make room first."""
    extras: list[PoolPick] = []
    seen = {p.spotify_id for p in pool.picks}
    pins = [(pin_roles.get(e.ref, FAVOURED_ROLE), e) for _, e in pinned.found]
    for role, entry in [*pins, *((FAVOURED_ROLE, e) for e in favoured.entries)]:
        if entry.spotify_id not in seen:
            seen.add(entry.spotify_id)
            extras.append(PoolPick(entry.ref, role, entry.spotify_id, entry.cover, entry.year))
    extras = extras[:MAX_POOL]
    return (*pool.picks[: MAX_POOL - len(extras)], *extras)


async def pick_composer(
    run: JobRun,
    given: DesignWithSongsInput,
    *,
    plan: PickPlan,
    slots: list[str],
    pin_slots: dict[int, int],
    favoured: Favoured,
    pinned: Pinned,
    registry: SearchRegistry,
    spotify: OpenConnection,
    keyring: KeyRing,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
) -> tuple[Picked, MusicStep]:
    """Steps 1 to 3; ``pin_slots`` maps a pin's index to its slot. The pool's music step is
    returned for the caller to close."""
    pool_config = stage_config(
        agent, config, turns=pool_module.MAX_TURNS, calls=pool_module.MAX_TOOL_CALLS
    )
    ctx = PoolContext(plan, registry, job_searcher(run, keyring, spotify))
    pool_outcome = await run_stage(
        JobStageHost(run, "pool_search"),
        provider,
        pool_stage(ctx),
        _message(ctx, slots, favoured, pinned),
        pool_config,
    )
    if not isinstance(pool_outcome.output, PoolResult) or not pool_outcome.output.picks:
        raise JobFailure("internal")
    pin_roles = {e.ref: slots[pin_slots[i]] for i, e in pinned.found if i in pin_slots}
    picks = _with_extras(pool_outcome.output, favoured, pinned, pin_roles)
    track_ids = tuple(p.spotify_id for p in picks)
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
        return await _choose(
            run,
            plan,
            picks,
            step,
            pin_slots,
            slots,
            favoured,
            pinned,
            pool_outcome,
            provider,
            agent,
            config,
        ), step
    except BaseException:
        step.close()
        raise


async def _choose(
    run: JobRun,
    plan: PickPlan,
    picks: tuple[PoolPick, ...],
    step: MusicStep,
    pin_slots: dict[int, int],
    slots: list[str],
    favoured: Favoured,
    pinned: Pinned,
    pool_outcome: Any,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
) -> Picked:
    analysis_map = step.data.analysis["map"]
    wanted = [(p.spotify_id, p.role) for p in picks]
    tracks, omitted = analysed_pool(analysis_map, wanted, {p.spotify_id: p.cover for p in picks})
    by_id = {t.spotify_id: t for t in tracks}
    kept: list[KeptSong] = []
    unmet = list(favoured.unmet) + list(pinned.unmet)
    for index, entry in pinned.found:
        track, slot = by_id.get(entry.spotify_id), pin_slots.get(index)
        if track is None or slot is None:
            unmet.append(
                InterpretationItem(
                    path=f"pinned_songs.{index}",
                    origin="not_as_asked",
                    reason_code=PINNED_NOT_FOUND,
                    value={"song": index},
                )
            )
            continue
        kept.append(
            KeptSong(
                slot,
                track.spotify_id,
                track.title,
                track.artist,
                slots[slot],
                track.bpm,
                track.length_sec,
                track.cover,
            )
        )
    kept.sort(key=lambda k: k.slot)
    if kept:
        kept_ids = {k.spotify_id for k in kept}
        tracks = [t for t in tracks if t.spotify_id not in kept_ids]
        taken = {k.slot for k in kept}
        roles = tuple(slots[s] for s in range(len(slots)) if s not in taken)
        if plan.kept:  # a swap's seed and pins never mix (``pin_slots`` is for a fill)
            raise JobFailure("internal")
        plan = PickPlan(plan.brief, plan.target_sec, plan.n, roles, tuple(kept))
    choose_ctx = ChooseContext.build(plan, tracks, omitted)  # pool_too_small
    choose_config = stage_config(
        agent, config, turns=choose_module.MAX_TURNS, calls=choose_module.MAX_TOOL_CALLS
    )
    outcome = await run_stage(
        JobStageHost(run, "choose"),
        provider,
        choose_stage(choose_ctx),
        choose_ctx.first_message(),
        choose_config,
    )
    if not isinstance(outcome.output, ChosenList):
        raise JobFailure("internal")
    fixed = {k.slot for k in plan.kept}
    chosen = prefer_favoured(
        outcome.output, tracks, {e.spotify_id for e in favoured.entries}, fixed
    )
    made = provenance(
        [pool_outcome, outcome],
        analysis_map,
        origin="design_with_songs",
        schema_version=SONG_SCHEMA_VERSION,
    )
    unmet += list_unmet(plan.brief.music_taste, picks, chosen)
    return Picked(chosen, made.model_dump(mode="json"), tuple(unmet[:MAX_UNMET]))
