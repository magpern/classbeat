"""A composer's **change** (ADR 0038 section 6, #1188), split out of ``classbeat.songs.composer``:
the waiting list's songs are kept, written and analysed again, and designed with the user's
request on the waiting preview; a new list with the same songs and a new preview. When the
designed length is more than 10 % from the songs' total (``composer_design.fits_songs``), it is a
change of shape: the ordinary sketch revise (``design.revise_soundless``, ``sketch=True``), the
new sketch current, the job complete, and the page starts the fill hand-off again for it.
"""

from typing import Any, Final

from classbeat.agent import ChosenList, provenance
from classbeat.agent.choose_songs import ChosenSong
from classbeat.agent.schemas import SONG_SCHEMA_VERSION
from classbeat.connections import OpenConnection
from classbeat.design.kind import Runtime, group_model
from classbeat.design.revise import ReviseDraftInput
from classbeat.design.revise_base import load as load_base
from classbeat.design.revise_soundless import run_soundless
from classbeat.jobs import Complete, JobFailure, JobRun, Outcome
from classbeat.songs import composer_design
from classbeat.songs.composer_common import (
    await_list,
    brief_of,
    dismissed_of,
    sources_of_list,
    waiting_list,
)
from classbeat.songs.composer_pick import Picked
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import DesignWithSongsInput

ORIGIN: Final = "design_with_songs"


async def run_change(
    run: JobRun,
    given: DesignWithSongsInput,
    runtime: Runtime,
    spotify: OpenConnection,
    sketch: Any,
    songs: SongConfig,
) -> Outcome:
    if given.request is None or given.request_message_id is None:
        raise JobFailure("internal")
    seeded, waiting = await waiting_list(run, given)
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
            brief=brief_of(sketch),
            base_content=preview.content,
            request=given.request,
            track_ids=waiting.track_ids,
            provider=design.provider,
            agent=design.agent,
            config=songs,
            gates=given.plan_gates,
            dismissed=dismissed_of(sketch),
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
    picked = Picked(
        chosen, made_by.model_dump(mode="json"), tuple(content.unmet), sources_of_list(content)
    )
    return await_list(given, spotify, picked, made, seeded.revision_id)


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
