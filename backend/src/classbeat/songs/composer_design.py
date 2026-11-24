"""The composer's ``design`` stage before the wait (ADR 0038 sections 5 and 6, #1188).

The class is designed on the chosen songs **before** the approval, and the result is the list's
``preview`` (``songs.preview_model``), never a draft revision:

1. **The songs' music.** The chosen songs are written to the user's private working copy in play
   order (``playlists.write_pool``, the pool's own consent, under a key of their own ids) and
   analysed by MOWL with the pool's sync wait (``design.music.load_music``): an analysis of
   exactly these songs in this order, like the final playlist's in phase 2; then every song on
   spotdown's sections from the list's record (``phase2_spotdown.listed_music``, stage D, #1386;
   never MOWL's), as phase 2 does, so ``preview.music`` compares like with like
   (``preview_model.song_times``). Nothing of it is stored but the
   preview's small ``music``.
2. **The design.** The ``revise`` stage (ADR 0007 section 3), as ``revise_songs``' phase 2 runs it,
   with ``songs_changed``: the base is the sketch (a fill) or the waiting list's preview (a swap
   or a change, so changes the user made stay), the music is step 1's, and the request is the
   user's words for a change or a fixed instruction for a fill or swap (``FILL_REQUEST``,
   ``SWAP_REQUEST``): keep the shape, re-time it to these songs. No new prompt: the stage's
   system prompt and tools are unchanged, and the request is data in its first message.
3. **The preview**: the stage's content (fitted and validated server-side, as a draft's,
   ``revision.checked_content``), the songs' fingerprint and spans, and the run's
   ``agent_runs`` id.

``fits_songs`` is the change's number test (section 6): the designed length against the songs'
total, more than ``RESHAPE_TOLERANCE`` (10 %) apart is a reshape.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from classbeat.agent import AgentConfig, Brief, JobStageHost, run_stage
from classbeat.agent.interpretation_record import InterpretationItem
from classbeat.agent.loop import StageOutcome
from classbeat.agent.provider import ModelProvider
from classbeat.agent.revise import ReviseContext, ReviseResult, revise_stage
from classbeat.agent.stored_draft import stored_draft
from classbeat.connections import OpenConnection
from classbeat.crypto.keks import KeyRing
from classbeat.design import corrections, revision
from classbeat.design.guidance import rule_set_for
from classbeat.design.interpreted import shape_off_items
from classbeat.design.music import MusicStep, load_music
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations import attach_spotify_playlist
from classbeat.plan_gates import PlanGates
from classbeat.playlists import write_pool
from classbeat.songs import song_list
from classbeat.songs.composer_slots_policy import sketch_seconds
from classbeat.songs.config import SongConfig
from classbeat.songs.phase2_spotdown import listed_music
from classbeat.songs.pick import stage_config
from classbeat.songs.preview_model import Preview, PreviewMusic, song_times

FILL_REQUEST: Final = (
    "Put these songs into this class: keep its shape (its segments, efforts and order) and "
    "re-time its intervals to the songs."
)
SWAP_REQUEST: Final = (
    "Some songs were swapped: keep the class as it is and re-time its intervals to the songs."
)
FIT_REQUEST: Final = (  # phase 2's re-design (ADR 0038 section 7, ``composer_phase2``)
    "The final playlist's songs are timed a little differently: keep the class as it is and "
    "re-time its intervals to the songs."
)
RESHAPE_TOLERANCE: Final = 0.10


@dataclass
class Designed:
    preview: Preview
    step: MusicStep  # the songs' analysis; the caller closes it
    outcome: StageOutcome
    note: str
    # The sections of the ride shape the design still misses after its corrections: Not as asked
    # items (``shape.N``, ``shape_off``) the list carries to the approval (#1493).
    unmet: tuple[InterpretationItem, ...] = ()


async def songs_music(
    run: JobRun,
    keyring: KeyRing,
    spotify: OpenConnection,
    mowl_connection_id: object,
    track_ids: tuple[str, ...],
    config: SongConfig,
    provenance: Mapping[str, Any],
) -> MusicStep:
    """Step 1 of the module docstring; ``provenance``: the new list's, with its spotdown record."""
    await run.stage("pool_write")
    written = await write_pool(run, keyring, spotify, track_ids)
    await run.checkpoint()
    connection = run.ctx.connection("mowl")
    if connection is None or connection != mowl_connection_id:
        raise JobFailure("not_connected")
    await attach_spotify_playlist(run, keyring, written.playlist_id, connection_id=connection)
    await run.stage("pool_analyse")
    step = await load_music(
        run,
        keyring,
        written.playlist_id,
        connection_id=connection,
        expect=track_ids,
        sync_wait_seconds=config.sync_wait_seconds,
    )
    analysed = tuple(str(t["spotify_id"]) for t in step.data.analysis["map"]["tracks"])
    try:
        if analysed != track_ids:  # the songs' own order, as phase 2 checks the final playlist
            raise JobFailure("playlist_mismatch")
        return await listed_music(run, track_ids, provenance, step)  # stage D, #1386
    except BaseException:
        step.close()
        raise


async def design_preview(
    run: JobRun,
    step: MusicStep,
    *,
    brief: Brief,
    base_content: dict[str, object],
    request: str,
    track_ids: tuple[str, ...],
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
    gates: PlanGates | None,
    dismissed: tuple[str, ...] = (),
) -> Designed:
    """Steps 2 and 3 of the module docstring; the caller owns ``step``."""
    await run.stage("design")
    outcome, revised, _ = await design_on(
        run, step, brief, base_content, request, provider, agent, config, gates, dismissed
    )
    content = revision.checked_content(revised.result)[0]  # no music: the list is not stored yet
    try:
        preview = Preview(
            content=content,
            music=PreviewMusic(
                fingerprint=song_list.fingerprint(track_ids),
                songs=song_times(step.data.analysis["map"]),
            ),
            design_run_id=outcome.run_id,
        )
    except (ValidationError, ValueError):
        raise JobFailure("invalid_output") from None
    missed = shape_off_items(brief, revised.result.unmet)
    return Designed(preview, step, outcome, revised.note, tuple(missed))


async def design_on(
    run: JobRun,
    step: MusicStep,
    brief: Brief,
    base_content: dict[str, object],
    request: str,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
    gates: PlanGates | None,
    dismissed: tuple[str, ...],
) -> tuple[StageOutcome, ReviseResult, dict[str, Any]]:
    """Step 2: the ``revise`` stage on ``step``'s music with ``base_content`` as its base (also
    phase 2's re-design, ``composer_phase2``); the run, its result and the map it fitted to."""
    try:
        base_draft = stored_draft(base_content, "cycling")
    except ValidationError:
        raise JobFailure("internal") from None
    rules = rule_set_for(brief, dismissed)
    await corrections.attach(run, step)
    stage = stage_config(
        agent, config, turns=agent.limits.max_turns, calls=agent.limits.max_tool_calls
    )
    ctx = ReviseContext.build(
        brief,
        step.data,
        base_draft,
        (),
        request,
        music_changed=True,
        songs_changed=True,
        rules=rules,
        gates=gates,
    )
    outcome = await run_stage(
        JobStageHost(run, "design"), provider, revise_stage(ctx), ctx.first_message(), stage
    )
    if not isinstance(outcome.output, ReviseResult):
        raise JobFailure("internal")
    return outcome, outcome.output, ctx.map


def fits_songs(preview: Preview, songs_total_sec: int) -> bool:
    """Whether the designed class's length is within 10 % of the songs' total (section 6)."""
    designed = sketch_seconds(preview.content)
    return abs(designed - songs_total_sec) <= RESHAPE_TOLERANCE * songs_total_sec
