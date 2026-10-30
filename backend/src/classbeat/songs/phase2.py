"""Phase 2 of a song pick (ADR 0008 sections 2, 4 and 6; issue SJ2): from the approved
``song_list`` to the final private playlist, its analysis in MOWL and the class's draft.

It runs after the user's Approve (the same job, ``approved``) or for a **resume** (a new job,
``mode = "resume"``, admitted against a receipt, ``classbeat.songs.admit``). Stages:

1. ``prepare`` (one fenced transaction, the discipline of ADR 0007 section 5.2): the job's own
   class; the list to build is the job's ``approval_revision_id`` (approved) or the resume's named
   list, read from the database, never from anything the browser sent; it must still be the
   class's **newest** ``song_list`` (``invalid_input`` otherwise: superseded) and still hash to
   its stored ``content_hash`` (``song_list.load``; ``invalid_input``); the Spotify account it was
   made for must be the connected one (``account_changed``); both connections must be the job's
   and ``connected`` (``not_connected``). The approved phase also requires both connections to be
   the **same generations** phase 1 recorded: a Spotify or MOWL reconnect between the pick and
   the run ends the job ``not_connected`` with no write (ADR 0008 Consequences: the approved list
   stays resumable, and a resume uses the connections as they are now). The playlist's tracks
   and order come **only from the stored revision**.
2. ``create``: ``classbeat.playlists.create_final_playlist`` (key ``<job_id>:final-create``,
   private, not collaborative; its ``on_succeeded`` inserts the ``class_playlists`` row), named
   after the class. **Skipped** when the class already has a live, ``verified`` playlist for this
   list on this Spotify account (a resume after a later failure, or a retried attempt): it is
   reused, never created twice. A retried attempt of the same job finds its succeeded create by
   key and gets the same playlist back.
3. ``fill``: ``fill_final_playlist`` (key ``<job_id>:final-fill``): owner check, absolute write,
   **read back** and compared with the approved ids in order; any difference is
   ``playlist_mismatch`` (failed, nothing designed). Skipped for a verified playlist.
4. ``attach``: ``mowl_attach_spotify_playlist`` of the final playlist (``spotify:<id>``).
5. ``music``: the design music step on it, waiting (``CLASSBEAT_SPOTIFY_POOL_SYNC_WAIT_SECONDS``)
   for MOWL to list the approved ids; then the analysed track list must be **exactly the
   approved ids in the approved order** (``playlist_mismatch`` otherwise, also when MOWL did not
   catch up in time: the user creates it again from the page).
6. ``design``: the ``design`` stage (ADR 0006 section 3) on that music, unchanged, within the
   song kinds' job ceiling (``CLASSBEAT_SONG_JOB_COST_MICROS``, across every stage and attempt).
7. The final fenced transaction (``Complete``): both connections held again, the MOWL link still
   the one analysed, the list still the newest and the class playlist still live and verified;
   then the ``draft`` revision (``music.song_list_id``), the class's first design adopted by
   compare-and-set (``brief``, ``music_source`` with ``origin: "song_pick"`` and
   ``class_playlist_id``, ``current_revision_id``), and ``class_playlists.mowl_playlist_id``.

**Failures keep what exists**: the created and verified playlist, its row, the MOWL link; a retry
reuses succeeded operations by key. An uncertain create or fill ends the job ``uncertain_write``
for the user's Check again / Do it anyway on ``/api/external-operations`` (final rows carry the
class); after that, a resume creates anew under its own key. A deleted class deletes the job
(cascade): the next fenced step finds no row and the run stops. The credit commits only on
``completed`` (settle) and is released on any other end.

Nothing here logs or sends a model an id: the design stage gets the music summary only.
"""

import logging
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text

from classbeat.agent import (
    AgentConfig,
    DesignContext,
    DesignResult,
    JobStageHost,
    design_stage,
    run_stage,
)
from classbeat.agent.provider import ModelProvider
from classbeat.connections import OpenConnection, hold, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.design import revision
from classbeat.design.music import MusicStep, load_music
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun
from classbeat.operations import attach_spotify_playlist
from classbeat.playlists import (
    ClassPlaylist,
    create_final_playlist,
    fill_final_playlist,
    open_spotify,
)
from classbeat.playlists import common as playlist_common
from classbeat.songs import song_list
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import DesignWithSongsInput
from classbeat.songs.pick import stage_config

logger = logging.getLogger(__name__)

PHASE_TWO: Final = ("prepare", "create", "fill", "attach", "music", "design")
ORIGIN: Final = "design_with_songs"
MUSIC_SOURCE: Final = "mowl_spotify_playlist"

_JOB = text(
    "SELECT j.class_id, j.approval_revision_id, c.title FROM jobs j JOIN classes c "
    "ON c.owner_id = j.owner_id AND c.id = j.class_id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id"
)
_NEWEST = text(
    "SELECT id FROM class_revisions WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND kind = 'song_list' ORDER BY number DESC LIMIT 1"
)
# A live, verified playlist of this list on this Spotify account, the newest first.
_VERIFIED = text(
    "SELECT id, playlist_id FROM class_playlists WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND revision_id = :revision_id AND spotify_user_id = :account "
    "AND state = 'verified' AND content_hash = :content_hash AND detached_at IS NULL "
    "ORDER BY created_at DESC LIMIT 1"
)
_LIVE_VERIFIED = text(
    "SELECT 1 FROM class_playlists WHERE owner_id = :owner_id AND class_id = :class_id "
    "AND id = :id AND playlist_id = :playlist_id AND revision_id = :revision_id "
    "AND state = 'verified' AND content_hash = :content_hash AND detached_at IS NULL"
)
_MOWL_ID = text(
    "UPDATE class_playlists SET mowl_playlist_id = :mowl_playlist_id, updated_at = now() "
    "WHERE owner_id = :owner_id AND class_id = :class_id AND id = :id AND detached_at IS NULL "
    "RETURNING id"
)


@dataclass(frozen=True)
class Prepared:
    class_id: UUID
    title: str
    revision_id: UUID
    stored: song_list.SongListRevision
    spotify: OpenConnection
    mowl: OpenConnection


async def _newest(fs: FencedSession, class_id: UUID) -> UUID | None:
    params = {"owner_id": fs.lease.owner_id, "class_id": class_id}
    return (await fs.db.execute(_NEWEST, params)).scalar_one_or_none()


async def prepare(run: JobRun, keyring: KeyRing, given: DesignWithSongsInput) -> Prepared:
    """Stage ``prepare`` (module docstring, step 1)."""
    await run.stage("prepare")
    if run.ctx.connection("mowl") != given.mowl_connection_id:
        raise JobFailure("not_connected")
    spotify = await open_spotify(run, keyring, given.spotify_connection_id)
    mowl = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if mowl.id != given.mowl_connection_id:
        raise JobFailure("not_connected")
    async with run.fenced() as fs:
        job = (await fs.db.execute(_JOB, fs.lease.params())).first()
        if job is None:
            raise JobFailure("invalid_input")
        if run.approved:
            revision_id = job.approval_revision_id
        else:
            revision_id = given.resume.song_list_revision_id if given.resume else None
        if revision_id is None or await _newest(fs, job.class_id) != revision_id:
            raise JobFailure("invalid_input")  # superseded (or the list is gone)
        stored = await song_list.load(fs.db, fs.lease.owner_id, job.class_id, revision_id)
        if stored is None:
            raise JobFailure("invalid_input")  # the row is not what was approved
        for opened in (spotify, mowl):
            if not await hold(fs.db, fs.lease.ctx, opened):
                raise JobFailure("not_connected")
    if stored.account != spotify.account:
        raise JobFailure("account_changed")
    pinned = stored.music.generations
    if run.approved and (
        pinned is None or pinned.spotify != spotify.generation or pinned.mowl != mowl.generation
    ):
        raise JobFailure("not_connected")  # reconnected since the pick: resume instead
    return Prepared(job.class_id, job.title, revision_id, stored, spotify, mowl)


def _tracks_hash(p: Prepared) -> bytes:
    """``class_playlists.content_hash`` of a playlist holding exactly the approved ids in order."""
    return playlist_common.ids_hash(p.stored.track_ids)


async def _verified(fs: FencedSession, p: Prepared) -> ClassPlaylist | None:
    row = (
        await fs.db.execute(
            _VERIFIED,
            {
                "owner_id": fs.lease.owner_id,
                "class_id": p.class_id,
                "revision_id": p.revision_id,
                "account": p.spotify.account,
                "content_hash": _tracks_hash(p),
            },
        )
    ).first()
    return None if row is None else ClassPlaylist(row.id, row.playlist_id)


def playlist_name(title: str) -> str:
    """The final playlist's name: the class's title, printable, at most 100 characters."""
    cleaned = "".join(c if c.isprintable() else " " for c in title).strip()[:100]
    return cleaned or "classbeat class"


async def build_playlist(run: JobRun, keyring: KeyRing, p: Prepared) -> ClassPlaylist:
    """Stages ``create`` and ``fill`` (steps 2 and 3): the verified final playlist."""
    await run.stage("create")
    async with run.fenced() as fs:
        reused = await _verified(fs, p)
    if reused is not None:
        logger.info("final playlist reused", extra={"job_id": str(run.lease.job_id)})
        return reused
    made = await create_final_playlist(
        run,
        keyring,
        p.spotify,
        class_id=p.class_id,
        revision_id=p.revision_id,
        content_hash=p.stored.content_hash,
        name=playlist_name(p.title),
    )
    await run.stage("fill")
    await fill_final_playlist(
        run,
        keyring,
        p.spotify,
        class_id=p.class_id,
        class_playlist_id=made.id,
        track_ids=p.stored.track_ids,
    )
    return made


def check_music(step: MusicStep, p: Prepared) -> None:
    """The analysed track list is exactly the approved ids, in order (``playlist_mismatch``)."""
    tracks = step.data.analysis["map"]["tracks"]
    if tuple(str(t.get("spotify_id")) for t in tracks) != p.stored.track_ids:
        raise JobFailure("playlist_mismatch")


async def _music(
    run: JobRun,
    keyring: KeyRing,
    given: DesignWithSongsInput,
    p: Prepared,
    final: ClassPlaylist,
    config: SongConfig,
) -> MusicStep:
    await run.stage("attach")
    await attach_spotify_playlist(
        run, keyring, final.playlist_id, connection_id=given.mowl_connection_id
    )
    await run.stage("music")
    try:
        step = await load_music(
            run,
            keyring,
            final.playlist_id,
            connection_id=given.mowl_connection_id,
            expect=p.stored.track_ids,
            sync_wait_seconds=config.sync_wait_seconds,
        )
    except JobFailure as failure:
        if failure.category == "pool_not_synced":  # the final playlist: MOWL shows other tracks
            raise JobFailure("playlist_mismatch") from None
        raise
    try:
        if step.generation != p.mowl.generation:
            raise JobFailure("not_connected")  # MOWL reconnected during the run
        check_music(step, p)
    except BaseException:
        step.close()
        raise
    return step


def music_source(final: ClassPlaylist) -> dict[str, Any]:
    """``classes.music_source`` of a class designed on picked songs (``ClassMusicSource``)."""
    return {
        "source": MUSIC_SOURCE,
        "spotify_playlist_id": final.playlist_id,
        "origin": "song_pick",
        "class_playlist_id": str(final.id),
    }


async def run_phase_two(
    run: JobRun,
    given: DesignWithSongsInput,
    *,
    keyring: KeyRing,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
) -> Complete:
    p = await prepare(run, keyring, given)
    final = await build_playlist(run, keyring, p)
    step = await _music(run, keyring, given, p, final, config)
    try:
        ctx = DesignContext.build(given.brief, step.data)
        design_config = stage_config(
            agent, config, turns=agent.limits.max_turns, calls=agent.limits.max_tool_calls
        )
        outcome = await run_stage(
            JobStageHost(run), provider, design_stage(ctx), ctx.first_message(), design_config
        )
        if not isinstance(outcome.output, DesignResult):
            raise JobFailure("internal")
        draft = revision.build(
            outcome.output, outcome, ctx.map, step, origin=ORIGIN, song_list_id=p.revision_id
        )
    except BaseException:
        step.close()
        raise
    mowl_playlist_id = step.data.playlist_id

    async def store(fs: FencedSession) -> UUID:
        """The final transaction (module docstring, step 7)."""
        if not await hold(fs.db, fs.lease.ctx, p.spotify):
            raise JobFailure("not_connected")
        await step.still_current(fs)
        owner = {"owner_id": fs.lease.owner_id, "class_id": p.class_id}
        if await _newest(fs, p.class_id) != p.revision_id:
            raise JobFailure("invalid_input")
        live = await fs.db.execute(
            _LIVE_VERIFIED,
            {
                **owner,
                "id": final.id,
                "playlist_id": final.playlist_id,
                "revision_id": p.revision_id,
                "content_hash": _tracks_hash(p),
            },
        )
        if live.first() is None:
            raise JobFailure("not_connected")  # Disconnect detached it meanwhile
        revision_id = await draft.store(fs)
        await revision.adopt_first(
            fs, revision_id, given.brief.model_dump(mode="json"), music_source(final)
        )
        marked = await fs.db.execute(
            _MOWL_ID, {**owner, "id": final.id, "mowl_playlist_id": mowl_playlist_id}
        )
        if marked.first() is None:
            raise JobFailure("internal")
        return revision_id

    # The runner releases the step once the attempt is over, whether ``store`` ran or not (a
    # cancel, a lost lease or shutdown before it; #122 item 4).
    return Complete(store, release=step.close)
