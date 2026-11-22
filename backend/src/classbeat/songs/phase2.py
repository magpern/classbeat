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
   and ``connected`` (``not_connected``); no final-playlist write of the class may be undecided
   (``uncertain_write``, ``orphans.playlist_unresolved``, behind ``admit``): any ``uncertain``
   row, including the job's own, so a retry of the same job also stops on its own uncertain
   create or fill, as Save's rule does; only the job's own ``pending`` rows are its to resolve by
   key. For ``revise_songs`` the base draft must still be current (``invalid_input``). The
   approved phase also requires both connections to be
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
   catch up in time: the user creates it again from the page). Then, for a song the list's
   ``spotdown_analysis`` record marks, spotdown's sections in place of MOWL's, stamped
   ``approximate`` (``phase2_spotdown``; SD-P6, #1303); always, there is no switch.
6. ``design``: the ``design`` stage (ADR 0006 section 3) on that music, unchanged, within the
   song kinds' job ceiling (``CLASSBEAT_SONG_JOB_COST_MICROS``, across every stage and attempt).
7. The final fenced transaction (``Complete``): both connections held again, the MOWL link still
   the one analysed, the list still the newest and the class playlist still live and verified;
   then the ``draft`` revision (``music.song_list_id``), the class's first design adopted by
   compare-and-set (``brief``, ``music_source`` with ``origin: "song_pick"`` and
   ``class_playlist_id``, ``current_revision_id``), and ``class_playlists.mowl_playlist_id``.
   A class whose ``music_source`` is ``none`` (a soundless class, #530) rolls it all back
   ``invalid_input`` (``music_kind.check_stored``): nothing is written over a ``none`` source.

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
from typing import Final
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
from classbeat.design import corrections, music_kind, revision, sport
from classbeat.design.guidance import rule_set_for
from classbeat.design.music import MusicStep, load_music
from classbeat.jobs import Complete, FencedSession, JobFailure, JobRun
from classbeat.operations import attach_spotify_playlist, orphans
from classbeat.playlists import (
    ClassPlaylist,
    create_final_playlist,
    fill_final_playlist,
    open_spotify,
)
from classbeat.songs import song_list
from classbeat.songs.config import SongConfig
from classbeat.songs.inputs import DesignWithSongsInput, SongJob
from classbeat.songs.phase2_final import final_checks, mark_mowl, music_source, newest, tracks_hash
from classbeat.songs.phase2_spotdown import spotdown_music
from classbeat.songs.pick import stage_config

logger = logging.getLogger(__name__)

PHASE_TWO: Final = ("prepare", "create", "fill", "attach", "music", "design")
ORIGIN: Final = "design_with_songs"

_JOB = text(
    "SELECT j.class_id, j.approval_revision_id, c.title FROM jobs j JOIN classes c "
    "ON c.owner_id = j.owner_id AND c.id = j.class_id "
    "WHERE j.id = :job_id AND j.owner_id = :owner_id"
)
# A live, verified playlist of this list on this Spotify account, the newest first.
_VERIFIED = text(
    "SELECT id, playlist_id FROM class_playlists WHERE owner_id = :owner_id "
    "AND class_id = :class_id AND revision_id = :revision_id AND spotify_user_id = :account "
    "AND state = 'verified' AND content_hash = :content_hash AND detached_at IS NULL "
    "ORDER BY created_at DESC LIMIT 1"
)


@dataclass(frozen=True)
class Prepared:
    class_id: UUID
    title: str
    revision_id: UUID
    stored: song_list.SongListRevision
    spotify: OpenConnection
    mowl: OpenConnection


_CURRENT = text(
    "SELECT current_revision_id FROM classes WHERE owner_id = :owner_id AND id = :class_id"
)


async def prepare(
    run: JobRun, keyring: KeyRing, given: SongJob, *, base_revision_id: UUID | None = None
) -> Prepared:
    """Stage ``prepare`` (module docstring, step 1). ``base_revision_id`` (``revise_songs``): the
    draft the change starts from must still be the class's current revision (``invalid_input``),
    checked in the same transaction, before any Spotify write."""
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
        if revision_id is None or await newest(fs, job.class_id) != revision_id:
            raise JobFailure("invalid_input")  # superseded (or the list is gone)
        stored = await song_list.load(fs.db, fs.lease.owner_id, job.class_id, revision_id)
        if stored is None:
            raise JobFailure("invalid_input")  # the row is not what was approved
        if await orphans.playlist_unresolved(fs.db, fs.lease.ctx, job.class_id, fs.lease.job_id):
            raise JobFailure("uncertain_write")  # behind admit: a final write is undecided
        if base_revision_id is not None:
            params = {"owner_id": fs.lease.owner_id, "class_id": job.class_id}
            if (await fs.db.execute(_CURRENT, params)).scalar_one_or_none() != base_revision_id:
                raise JobFailure("invalid_input")  # the base is no longer the current draft
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


async def _verified(fs: FencedSession, p: Prepared) -> ClassPlaylist | None:
    row = (
        await fs.db.execute(
            _VERIFIED,
            {
                "owner_id": fs.lease.owner_id,
                "class_id": p.class_id,
                "revision_id": p.revision_id,
                "account": p.spotify.account,
                "content_hash": tracks_hash(p),
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
    given: SongJob,
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


async def final_music(
    run: JobRun,
    keyring: KeyRing,
    given: SongJob,
    config: SongConfig,
    *,
    base_revision_id: UUID | None = None,
) -> tuple[Prepared, ClassPlaylist, MusicStep]:
    """Steps 1 to 5 (both kinds): the approved list, its verified final playlist and its music.
    The caller owns the step from here (``close`` it, or hand it to the outcome's ``release``)."""
    p = await prepare(run, keyring, given, base_revision_id=base_revision_id)
    final = await build_playlist(run, keyring, p)
    step = await _music(run, keyring, given, p, final, config)
    try:  # SD-P6 (#1303): spotdown's sections for the marked songs (always; no fallback)
        return p, final, await spotdown_music(run, p.stored, step)
    except BaseException:
        step.close()
        raise


async def run_phase_two(
    run: JobRun,
    given: DesignWithSongsInput,
    *,
    keyring: KeyRing,
    provider: ModelProvider,
    agent: AgentConfig,
    config: SongConfig,
) -> Complete:
    if given.brief.sport != "cycling":
        raise JobFailure("invalid_input")  # ADR 0018: the input refuses it; never a running pick
    p, final, step = await final_music(run, keyring, given, config)
    try:
        rules = rule_set_for(given.brief)
        await corrections.attach(run, step)  # the class's beat-one corrections (ADR 0007)
        ctx = DesignContext.build(given.brief, step.data, rules, given.plan_gates)  # #496
        design_config = stage_config(
            agent, config, turns=agent.limits.max_turns, calls=agent.limits.max_tool_calls
        )
        outcome = await run_stage(
            JobStageHost(run), provider, design_stage(ctx), ctx.first_message(), design_config
        )
        if not isinstance(outcome.output, DesignResult):
            raise JobFailure("internal")
        draft = revision.build(
            outcome.output,
            outcome,
            ctx.map,
            step,
            origin=ORIGIN,
            song_list_id=p.revision_id,
            rules=rules.provenance() if rules else None,
        )
    except BaseException:
        step.close()
        raise
    mowl_playlist_id = step.data.playlist_id

    async def store(fs: FencedSession) -> UUID:
        """The final transaction (module docstring, step 7)."""
        await final_checks(fs, p, final, step)
        revision_id = await draft.store(fs)
        # Never over a ``none`` source (#530): checked before ``adopt_first``, which writes
        # ``music_source`` whenever the class has no current revision yet.
        await music_kind.check_stored(fs, soundless=False)
        await revision.adopt_first(fs, revision_id, given.brief.stored(), music_source(final))
        await sport.check_stored(fs, given.brief.sport)  # the class's sport never changes
        await mark_mowl(fs, p, final, mowl_playlist_id)
        return revision_id

    # The runner releases the step once the attempt is over, whether ``store`` ran or not (a
    # cancel, a lost lease or shutdown before it; #122 item 4).
    return Complete(store, release=step.close)
