"""The composer's ``design`` stage before the wait (ADR 0038 sections 5 and 6, #1188).

The class is designed on the chosen songs **before** the approval, and the result is the list's
``preview`` (``songs.preview_model``), never a draft revision:

1. **The songs' music.** The chosen songs are written to the user's private working copy in play
   order (``playlists.write_pool``, the pool's own consent, under a key of their own ids) and
   analysed by MOWL with the pool's sync wait (``design.music.load_music``): an analysis of
   exactly these songs in this order, like the final playlist's in phase 2, so ``preview.music``
   compares like with like (``preview_model.song_times``). Nothing of it is stored but the
   preview's small ``music``.
2. **The design.** The ``revise`` stage (ADR 0007 section 3), as ``revise_songs``' phase 2 runs it,
   with ``songs_changed``: the base is the sketch (a fill) or the waiting list's preview (a swap
   or a change, so changes the user made stay), the music is step 1's, and the request is the
   user's words for a change or a fixed instruction for a fill or swap (``FILL_REQUEST``,
   ``SWAP_REQUEST``): keep the shape, re-time it to these songs. No new prompt: the stage's
   system prompt and tools are unchanged, and the request is data in its first message.
3. **The preview**: the stage's content (fitted and validated server-side, as a draft's,
   ``revision.content_and_music``), the songs' fingerprint and spans, and the run's
   ``agent_runs`` id.

``fits_songs`` is the change's number test (section 6): the designed length against the songs'
total, more than ``RESHAPE_TOLERANCE`` (10 %) apart is a reshape.
"""

from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from classbeat.agent import AgentConfig, Brief, JobStageHost, run_stage
from classbeat.agent.loop import StageOutcome
from classbeat.agent.provider import ModelProvider
from classbeat.agent.revise import ReviseContext, ReviseResult, revise_stage
from classbeat.agent.stored_draft import stored_draft
from classbeat.connections import OpenConnection
from classbeat.crypto.keks import KeyRing
from classbeat.design import corrections, revision
from classbeat.design.guidance import rule_set_for
from classbeat.design.music import MusicStep, load_music
from classbeat.jobs import JobFailure, JobRun
from classbeat.operations import attach_spotify_playlist
from classbeat.plan_gates import PlanGates
from classbeat.playlists import write_pool
from classbeat.songs import song_list
from classbeat.songs.composer_slots_policy import sketch_seconds
from classbeat.songs.config import SongConfig
from classbeat.songs.pick import stage_config
from classbeat.songs.preview_model import Preview, PreviewMusic, song_times

FILL_REQUEST: Final = (
    "Put these songs into this class: keep its shape (its segments, efforts and order) and "
    "re-time its intervals to the songs."
)
SWAP_REQUEST: Final = (
    "Some songs were swapped: keep the class as it is and re-time its intervals to the songs."
)
RESHAPE_TOLERANCE: Final = 0.10


@dataclass
class Designed:
    preview: Preview
    step: MusicStep  # the songs' analysis; the caller closes it
    outcome: StageOutcome
    note: str


async def songs_music(
    run: JobRun,
    keyring: KeyRing,
    spotify: OpenConnection,
    mowl_connection_id: object,
    track_ids: tuple[str, ...],
    config: SongConfig,
) -> MusicStep:
    """Step 1 of the module docstring."""
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
    if analysed != track_ids:  # the songs' own order, as phase 2 checks the final playlist
        step.close()
        raise JobFailure("playlist_mismatch")
    return step


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
    revised = outcome.output
    content = revision.content_and_music(revised.result, ctx.map, step, None)[0]
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
    return Designed(preview, step, outcome, revised.note)


def fits_songs(preview: Preview, songs_total_sec: int) -> bool:
    """Whether the designed class's length is within 10 % of the songs' total (section 6)."""
    designed = sketch_seconds(preview.content)
    return abs(designed - songs_total_sec) <= RESHAPE_TOLERANCE * songs_total_sec
