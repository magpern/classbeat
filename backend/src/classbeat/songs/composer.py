"""Phase 1 of the composer's ``design_with_songs`` (ADR 0038 sections 5, 6 and 12, #1188): from
the class's sketch to a ``song_list`` revision (content ``v: 3``, with the class designed on its
songs as its ``preview``) and the wait for the user's one approval.

- **fill**: the attached playlists are read (favoured) and the pinned songs verified
  (``composer_sources``); the sketch's song slots (``composer_slots_policy``) size the pick;
  ``composer_pick`` runs ``pool_search``, ``pool_write``, ``pool_analyse`` and ``choose``; then
  ``composer_design`` designs on the chosen songs with the sketch as its base.
- **swap**: the replace of the waiting list's slots (``seed``, as ``revise_songs`` replaces), with
  the attached playlists favoured again, then the design with the waiting list's preview as its
  base, so the user's earlier changes stay.
- **change**: no pick: the waiting list's songs are kept, written and analysed again (a new run of
  ``pool_write`` and ``pool_analyse``), then designed with the user's request on the waiting
  preview. When the designed length is more than 10 % from the songs' total
  (``composer_design.fits_songs``), the change is a change of shape: the ordinary sketch revise
  runs instead (``design.revise_soundless``, ``sketch=True``), the new sketch becomes current and
  the job completes; the page then starts the fill hand-off again for it.

The store (``AwaitApproval``, one fenced transaction): the Spotify connection still the one the
pick used, the songs' music step still current, the sketch **still the class's current revision**
(``invalid_input`` otherwise), then the list. **No draft revision is stored and no pointer moves**:
the sketch stays current while the list waits, is approved, replaced, cancelled or expires.
An approved composer job's phase 2 (the final playlist, ``fit``, the first real draft and
``save_pending``) is ``classbeat.songs.composer_phase2`` (#1189).
"""

from typing import Any, Final
from uuid import UUID

from classbeat.agent import AgentConfig, PickPlan, SearchRegistry
from classbeat.agent.provider import ModelProvider
from classbeat.connections import OpenConnection
from classbeat.design.kind import Runtime, group_model
from classbeat.jobs import AwaitApproval, JobFailure, JobRun, Outcome
from classbeat.playlists import open_spotify
from classbeat.songs import composer_design, config
from classbeat.songs.composer_change import run_change
from classbeat.songs.composer_common import (
    Sketch,
    await_list,
    brief_of,
    dismissed_of,
    read_sketch,
    sources_of_list,
    waiting_list,
)
from classbeat.songs.composer_pick import Picked, pick_composer
from classbeat.songs.composer_slots_policy import pin_slots, sketch_seconds, slot_roles
from classbeat.songs.composer_sources import (
    Favoured,
    Pinned,
    read_sources,
    sources_of,
    verify_pins,
)
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.pick_analyse import kept_timing

ORIGIN: Final = "design_with_songs"


async def run_composer(job_run: JobRun, given: DesignWithSongsInput, runtime: Runtime) -> Outcome:
    base = given.base_revision_id
    if base is None or given.class_id is None:
        raise JobFailure("internal")
    if job_run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    songs = config.current()
    sketch = await read_sketch(job_run, base)
    spotify = await open_spotify(job_run, runtime.keyring, given.spotify_connection_id)
    if spotify.account is None:
        raise JobFailure("not_connected")
    if given.mode == "change":
        return await run_change(job_run, given, runtime, spotify, sketch, songs)
    model = await group_model(runtime, job_run, given.route_key, "songs")
    if given.mode == "fill":
        picked, kept_parent = await _fill(job_run, given, spotify, sketch, model, songs, runtime)
        base_content: dict[str, Any] = sketch.content
        request = composer_design.FILL_REQUEST
    else:
        picked, kept_parent, base_content = await _swap(
            job_run, given, spotify, sketch, model, songs, runtime
        )
        request = composer_design.SWAP_REQUEST
    design = await group_model(runtime, job_run, given.route_key, "design")
    return await _designed(
        job_run,
        given,
        spotify,
        picked,
        base_content,
        request,
        kept_parent,
        sketch,
        design.provider,
        design.agent,
        songs,
        runtime,
    )


async def _fill(
    run: JobRun,
    given: DesignWithSongsInput,
    spotify: OpenConnection,
    sketch: Sketch,
    model: Any,
    songs: SongConfig,
    runtime: Runtime,
) -> tuple[Picked, UUID | None]:
    registry = SearchRegistry()
    keyring = runtime.keyring
    await run.stage("pool_search")
    favoured = (
        await read_sources(run, keyring, spotify, given.sources, registry)
        if given.sources
        else Favoured((), ())
    )
    pins = given.pinned or []
    pinned = await verify_pins(run, keyring, spotify, pins, registry) if pins else Pinned((), ())
    slots = slot_roles(sketch.content)
    placed = pin_slots([pins[i].at for i, _ in pinned.found], slots)
    at = {i: s for (i, _), s in zip(pinned.found, placed, strict=True) if s is not None}
    plan = PickPlan(brief_of(sketch), sketch_seconds(sketch.content), len(slots))
    picked, step = await pick_composer(
        run,
        given,
        plan=plan,
        slots=slots,
        pin_slots=at,
        favoured=favoured,
        pinned=pinned,
        registry=registry,
        spotify=spotify,
        keyring=keyring,
        provider=model.provider,
        agent=model.agent,
        config=songs,
    )
    step.close()  # the songs are analysed again, in their order, for the design
    return picked, None


async def _swap(
    run: JobRun,
    given: DesignWithSongsInput,
    spotify: OpenConnection,
    sketch: Sketch,
    model: Any,
    songs: SongConfig,
    runtime: Runtime,
) -> tuple[Picked, UUID | None, dict[str, Any]]:
    seeded, waiting = await waiting_list(run, given)
    registry = SearchRegistry()
    await run.stage("pool_search")
    favoured = (
        await read_sources(run, runtime.keyring, spotify, given.sources, registry)
        if given.sources
        else Favoured((), ())
    )
    brief = brief_of(sketch)
    content = waiting.content
    plan = PickPlan(brief, content.target_sec, len(content.songs), seeded.roles, seeded.kept)
    seed = given.seed
    removed = frozenset(
        content.songs[slot].spotify_id
        for slot in (seed.replace if seed is not None else [])
        if slot < len(content.songs)
    )  # the songs the swap takes out: never put back by the favoured rule (#1188 review B2)
    picked, step = await pick_composer(
        run,
        given,
        plan=plan,
        slots=[s.role for s in content.songs],
        pin_slots={},
        favoured=favoured,
        pinned=Pinned((), ()),
        registry=registry,
        spotify=spotify,
        keyring=runtime.keyring,
        provider=model.provider,
        agent=model.agent,
        config=songs,
        excluded=removed,
    )
    step.close()
    preview = content.preview
    if preview is None:
        raise JobFailure("internal")  # a composer job's list always has one
    earlier = sources_of_list(content)
    kept = {k.spotify_id: earlier[k.spotify_id] for k in seeded.kept if k.spotify_id in earlier}
    sources = sources_of(picked.chosen, favoured, Pinned((), ()), kept)
    carried = kept_timing(picked.provenance, seeded, picked.chosen)  # a kept song's entry, #1386
    picked = Picked(picked.chosen, carried, picked.unmet, sources)
    return picked, seeded.revision_id, preview.content


async def _designed(
    run: JobRun,
    given: DesignWithSongsInput,
    spotify: OpenConnection,
    picked: Picked,
    base_content: dict[str, Any],
    request: str,
    parent: UUID | None,
    sketch: Sketch,
    provider: ModelProvider,
    agent: AgentConfig,
    songs: SongConfig,
    runtime: Runtime,
) -> AwaitApproval:
    track_ids = tuple(s.spotify_id for s in picked.chosen.songs)
    step = await composer_design.songs_music(
        run, runtime.keyring, spotify, given.mowl_connection_id, track_ids, songs, picked.provenance
    )
    try:
        made = await composer_design.design_preview(
            run,
            step,
            brief=brief_of(sketch),
            base_content=base_content,
            request=request,
            track_ids=track_ids,
            provider=provider,
            agent=agent,
            config=songs,
            gates=given.plan_gates,
            dismissed=dismissed_of(sketch),
        )
    except BaseException:
        step.close()
        raise
    return await_list(given, spotify, picked, made, parent)
