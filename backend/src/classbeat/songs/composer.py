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
An approved composer job's phase 2 (``fit``, ``save_pending``) is #1189; until then it fails
closed in ``kind.run`` before any Spotify or MOWL call.
"""

from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from classbeat.agent import AgentConfig, ChosenList, PickPlan, SearchRegistry, provenance
from classbeat.agent.brief import Brief
from classbeat.agent.choose_songs import ChosenSong
from classbeat.agent.provider import ModelProvider
from classbeat.agent.schemas import SONG_SCHEMA_VERSION
from classbeat.connections import OpenConnection, hold
from classbeat.design.kind import Runtime, group_model
from classbeat.design.revise import ReviseDraftInput
from classbeat.design.revise_base import load as load_base
from classbeat.design.revise_soundless import run_soundless
from classbeat.design.revision_ready_policy import SKETCH
from classbeat.jobs import AwaitApproval, Complete, FencedSession, JobFailure, JobRun, Outcome
from classbeat.playlists import open_spotify
from classbeat.songs import composer_design, config, song_list
from classbeat.songs.composer_pick import Picked, pick_composer
from classbeat.songs.composer_slots_policy import pin_slots, sketch_seconds, slot_roles
from classbeat.songs.composer_sources import Favoured, Pinned, read_sources, verify_pins
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.pick import generations
from classbeat.songs.seed import SeedSpec, class_of, load_seeded

ORIGIN: Final = "design_with_songs"

_SKETCH = text(
    "SELECT r.content, r.content_hash, r.provenance ->> 'origin' AS origin, c.brief, "
    "c.dismissed_rules, c.current_revision_id = r.id AS current FROM classes c "
    "JOIN class_revisions r ON r.owner_id = c.owner_id AND r.class_id = c.id AND r.id = :base "
    "WHERE c.owner_id = :owner_id AND c.id = :class_id"
)
_STILL_CURRENT = text(
    "SELECT 1 FROM classes c JOIN jobs j ON j.owner_id = c.owner_id AND j.class_id = c.id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id AND c.current_revision_id = :base FOR SHARE"
)


async def _sketch(run: JobRun, base: UUID) -> Any:
    """The sketch, read through the job's own class: still a sketch and still current."""
    async with run.fenced() as fs:
        class_id = await class_of(fs)
        params = {"owner_id": fs.lease.owner_id, "class_id": class_id, "base": base}
        row = (await fs.db.execute(_SKETCH, params)).first()
    if row is None or row.origin != SKETCH or not row.current or not isinstance(row.content, dict):
        raise JobFailure("invalid_input")  # stale: the page starts again from the current sketch
    return row


async def still_current(fs: FencedSession, base: UUID) -> None:
    params = {"job_id": fs.lease.job_id, "owner_id": fs.lease.owner_id, "base": base}
    if (await fs.db.execute(_STILL_CURRENT, params)).first() is None:
        raise JobFailure("invalid_input")  # another sketch is current: this list is of no class


async def run_composer(job_run: JobRun, given: DesignWithSongsInput, runtime: Runtime) -> Outcome:
    base = given.base_revision_id
    if base is None or given.class_id is None:
        raise JobFailure("internal")
    if job_run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    songs = config.current()
    sketch = await _sketch(job_run, base)
    spotify = await open_spotify(job_run, runtime.keyring, given.spotify_connection_id)
    if spotify.account is None:
        raise JobFailure("not_connected")
    if given.mode == "change":
        return await _change(job_run, given, runtime, spotify, sketch, songs)
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
    sketch: Any,
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
    plan = PickPlan(_brief(sketch), sketch_seconds(sketch.content), len(slots))
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
    sketch: Any,
    model: Any,
    songs: SongConfig,
    runtime: Runtime,
) -> tuple[Picked, UUID | None, dict[str, Any]]:
    seeded, waiting = await _waiting(run, given)
    registry = SearchRegistry()
    await run.stage("pool_search")
    favoured = (
        await read_sources(run, runtime.keyring, spotify, given.sources, registry)
        if given.sources
        else Favoured((), ())
    )
    brief = _brief(sketch)
    content = waiting.content
    plan = PickPlan(brief, content.target_sec, len(content.songs), seeded.roles, seeded.kept)
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
    )
    step.close()
    preview = content.preview
    if preview is None:
        raise JobFailure("internal")  # a composer job's list always has one
    return picked, seeded.revision_id, preview.content


async def _waiting(
    run: JobRun, given: DesignWithSongsInput
) -> tuple[Any, song_list.SongListRevision]:
    seed = given.seed
    if seed is None:
        raise JobFailure("internal")
    spec = SeedSpec(seed.song_list_revision_id, tuple(seed.replace) or None)
    if given.mode == "change":
        spec = SeedSpec(seed.song_list_revision_id, None)  # only read: every song is kept
    seeded = await load_seeded(run, spec)
    async with run.fenced() as fs:
        class_id = await class_of(fs)
        found = await song_list.load(fs.db, fs.lease.owner_id, class_id, seed.song_list_revision_id)
    if seeded is None or found is None or found.content.preview is None:
        raise JobFailure("invalid_input")
    return seeded, found


def _brief(sketch: Any) -> Brief:
    try:
        return Brief.model_validate(sketch.brief)
    except ValidationError:
        raise JobFailure("internal") from None


async def _change(
    run: JobRun,
    given: DesignWithSongsInput,
    runtime: Runtime,
    spotify: OpenConnection,
    sketch: Any,
    songs: SongConfig,
) -> Outcome:
    if given.request is None or given.request_message_id is None:
        raise JobFailure("internal")
    seeded, waiting = await _waiting(run, given)
    content = waiting.content
    kept = tuple(
        ChosenSong(s.spotify_id, s.title, s.artist, s.role, s.bpm, s.length_sec, False, s.cover)
        for s in content.songs
    )
    chosen = ChosenList(
        kept,
        content.total_sec,
        content.target_sec,
        content.crossfade_sec,
        "replace",
        content.omitted_no_bpm,
        content.note,
    )
    preview = content.preview
    if preview is None:
        raise JobFailure("internal")
    design = await group_model(runtime, run, given.route_key, "design")
    step = await composer_design.songs_music(
        run, runtime.keyring, spotify, given.mowl_connection_id, waiting.track_ids, songs
    )
    try:
        made = await composer_design.design_preview(
            run,
            step,
            brief=_brief(sketch),
            base_content=preview.content,
            request=given.request,
            track_ids=waiting.track_ids,
            provider=design.provider,
            agent=design.agent,
            config=songs,
            gates=given.plan_gates,
            dismissed=_dismissed(sketch),
        )
    except BaseException:
        step.close()
        raise
    if not composer_design.fits_songs(made.preview, content.total_sec):
        step.close()
        return await _reshape(run, given, runtime, sketch)
    made_by = provenance(
        [made.outcome], step.data.analysis["map"], origin=ORIGIN, schema_version=SONG_SCHEMA_VERSION
    )
    picked = Picked(chosen, made_by.model_dump(mode="json"), tuple(content.unmet))
    return _await(given, spotify, picked, made, seeded.revision_id)


async def _reshape(
    run: JobRun, given: DesignWithSongsInput, runtime: Runtime, sketch: Any
) -> Complete:
    """A change of shape: the ordinary sketch revise, the new sketch current (module docstring)."""
    base = given.base_revision_id
    if base is None or given.class_id is None or given.request is None:
        raise JobFailure("internal")
    revise = ReviseDraftInput(
        class_id=given.class_id,
        base_revision_id=base,
        request=given.request,
        mowl_connection_id=given.mowl_connection_id,
        base_revision_hash=bytes(sketch.content_hash).hex(),
        request_message_id=given.request_message_id,
        plan_gates=given.plan_gates,
        route_key=given.route_key,
        sketch_base=True,
    )
    digest = bytes(sketch.content_hash).hex()
    loaded = await load_base(run, base, digest, runtime.context_requests, sketch=True)
    return await run_soundless(run, revise, loaded, runtime, sketch=True)


def _dismissed(sketch: Any) -> tuple[str, ...]:
    value = sketch.dismissed_rules
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


async def _designed(
    run: JobRun,
    given: DesignWithSongsInput,
    spotify: OpenConnection,
    picked: Picked,
    base_content: dict[str, Any],
    request: str,
    parent: UUID | None,
    sketch: Any,
    provider: ModelProvider,
    agent: AgentConfig,
    songs: SongConfig,
    runtime: Runtime,
) -> AwaitApproval:
    track_ids = tuple(s.spotify_id for s in picked.chosen.songs)
    step = await composer_design.songs_music(
        run, runtime.keyring, spotify, given.mowl_connection_id, track_ids, songs
    )
    try:
        made = await composer_design.design_preview(
            run,
            step,
            brief=_brief(sketch),
            base_content=base_content,
            request=request,
            track_ids=track_ids,
            provider=provider,
            agent=agent,
            config=songs,
            gates=given.plan_gates,
            dismissed=_dismissed(sketch),
        )
    except BaseException:
        step.close()
        raise
    return _await(given, spotify, picked, made, parent)


def _await(
    given: DesignWithSongsInput,
    spotify: OpenConnection,
    picked: Picked,
    made: composer_design.Designed,
    parent: UUID | None,
) -> AwaitApproval:
    step = made.step
    base = given.base_revision_id
    if base is None or spotify.account is None:
        step.close()
        raise JobFailure("internal")
    try:
        built = song_list.build(
            picked.chosen,
            account=spotify.account,
            provenance={**picked.provenance, "composer": given.mode},
            parent_id=parent,
            generations=generations(spotify, step),
            unmet=picked.unmet,
            preview=made.preview,  # invalid_output over 64 KB, before anything is stored
        )
    except BaseException:
        step.close()
        raise

    async def store(fs: FencedSession) -> tuple[UUID, bytes]:
        if not await hold(fs.db, fs.lease.ctx, spotify):
            raise JobFailure("not_connected")
        await step.still_current(fs)
        await still_current(fs, base)
        return await built.store(fs), built.content_hash

    return AwaitApproval(store, release=step.close)
