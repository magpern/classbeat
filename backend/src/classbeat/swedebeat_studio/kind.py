"""The ``studio_songs`` job kind (ADR 0036 sections 2 and 3): the studio's song stage, with **no
MOWL connection**. From the brief (or a list the user is changing) to a studio ``song_list``
revision and the wait for the user's one approval (ADR 0008 section 5).

- **Input** ``StudioSongsInput`` (``CONNECTIONS = ("spotify", "swedebeat")``); ``admit`` and
  ``approve_check`` are ``swedebeat_studio.admit``'s. The class's credit, as ``design_with_songs``
  (``consumes_credit``; an action passes it on, see ``swedebeat_studio.admit``).
- **Before any call** (the worker): the studio is on and the user's SwedeBeat preference too
  (``feature_off`` otherwise), the class is not soundless, a seed list still loads and still fits
  its action (``invalid_input``); then the Spotify connection and the SwedeBeat bearer are opened
  (``not_connected``, ``app_misconfigured`` without a gateway), in this job only.
- **The action**: ``pick`` and ``swap_anyway`` (``pick.pick``; from a search, or from the user's
  own Spotify playlist, read only, in the stage ``playlist_read``), ``undo`` and ``another``
  (``edit``). Their models are the ``songs`` stage group's.
- ``working_copy``: the pool playlist (the user's working copy) follows the list, in order
  (``playlists.write_pool``: the owner check, a replace of exactly those tracks, read back). The
  user's own playlists are never modified. Its state belongs to the new revision (ADR 0036
  section 9, SR C8; ``playlists.sync``): the revision's id is chosen first, the row is intended
  ``pending``, the write holds the pool's lock and re-checks the intended revision in each decide
  step, and the row ends ``synced`` or ``failed``. **A failed sync never fails the job**: the list
  is stored and waits for its approval, and the user sees the state with "Sync again". Only a
  lost lease ends the job here.
- **The store**, in the final fenced transaction: both connections still the ones the run used
  (``not_connected``), a class still with music, then the revision; the runner sets
  ``waiting_for_approval``.
- **Approved**: the SwedeBeat connection must still be the list's (its id and generation,
  ``not_connected`` otherwise), then the job completes. The approved list is what the per-track
  analysis and the design read (later stages, ADR 0036 section 11); nothing is written here.

Registered in ``classbeat.jobs.KINDS`` on import, by the worker and the web app.
"""

import uuid
from typing import Any, Final

from sqlalchemy import text

from classbeat.agent import provenance
from classbeat.agent.schemas import SONG_SCHEMA_VERSION
from classbeat.connections import hold
from classbeat.connections.preferences import uses_swedebeat_in
from classbeat.design import music_kind
from classbeat.design.kind import current_runtime, group_model
from classbeat.jobs import (
    KINDS,
    AwaitApproval,
    Complete,
    FencedSession,
    JobFailure,
    JobKind,
    JobRun,
)
from classbeat.jobs.kinds import Outcome
from classbeat.playlists import open_spotify, sync, write_pool
from classbeat.quota.route import with_plan, with_plan_run
from classbeat.songs import config as song_config
from classbeat.songs.search import job_searcher
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_match.kind import open_match_bearer
from classbeat.swedebeat_studio import song_list
from classbeat.swedebeat_studio.admit import KIND, admit, approve_check, studio_on
from classbeat.swedebeat_studio.edit import another, undone
from classbeat.swedebeat_studio.inputs import StudioSongsInput
from classbeat.swedebeat_studio.pick import pick
from classbeat.swedebeat_studio.song_list_model import StudioListContent
from classbeat.swedebeat_studio.stages import Steps
from classbeat.swedebeat_studio.swap_policy import askable, undoable

STAGES: Final = (
    "playlist_read", "pool_search", "pool_match", "swap", "choose", "working_copy",
)  # fmt: skip
_APPROVED = text(
    "SELECT class_id, approval_revision_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id"
)
_JOB_CLASS = text("SELECT class_id FROM jobs WHERE id = :job_id AND owner_id = :owner_id")


async def _prepare(run: JobRun, given: StudioSongsInput) -> song_list.StudioList | None:
    """The checks before any call; the seed list of an action, read again."""
    class_id = given.class_id
    async with run.fenced() as fs:
        on = await studio_on(fs.db) and await uses_swedebeat_in(fs.db, run.lease.owner_id)
        if on and class_id is not None:
            await music_kind.still_studio(fs, class_id)
        seed = None
        if on and given.seed is not None and class_id is not None:
            seed = await song_list.load(
                fs.db, run.lease.owner_id, class_id, given.seed.song_list_revision_id
            )
    if not on:
        raise JobFailure("feature_off")
    if given.action == "pick":
        return None
    position = given.the_seed.position
    fits = seed is not None and (
        (given.action == "swap_anyway" and seed.content.evidence.state == "suspected_unavailable")
        or (given.action == "undo" and position is not None and undoable(seed.content, position))
        or (given.action == "another" and position is not None and askable(seed.content, position))
    )
    if not fits:
        raise JobFailure("invalid_input")
    return seed


async def _approved(run: JobRun, given: StudioSongsInput) -> Complete:
    """The approved list's SwedeBeat connection, still the same one; nothing is written."""
    cfg = match_config.current()
    async with run.fenced() as fs:
        on = await studio_on(fs.db)
        job = (await fs.db.execute(_APPROVED, fs.lease.params())).one()
        stored = None
        if on and job.approval_revision_id is not None:
            stored = await song_list.load(
                fs.db, run.lease.owner_id, job.class_id, job.approval_revision_id
            )
    if not on:
        raise JobFailure("feature_off")
    if stored is None:
        raise JobFailure("invalid_input")
    bearer = await open_match_bearer(run, cfg, given.swedebeat_connection_id)
    pinned = stored.music
    if (
        str(bearer.connection.id) != pinned.swedebeat_connection_id
        or bearer.connection.generation != pinned.generations.swedebeat
    ):
        raise JobFailure("not_connected")
    return Complete()


def _provenance(steps: Steps | None, seed: song_list.StudioList | None) -> dict[str, Any]:
    if steps is not None and steps.runs:
        made = provenance(
            steps.runs, None, origin="studio_songs", schema_version=SONG_SCHEMA_VERSION
        )
        return made.model_dump(mode="json")
    return dict(seed.provenance) if seed is not None else {"origin": "studio_songs"}


async def run(job_run: JobRun) -> Outcome:
    given = job_run.input
    runtime = current_runtime()
    if runtime is None or not isinstance(given, StudioSongsInput):
        raise JobFailure("internal")
    if job_run.approved:
        return await _approved(job_run, given)
    seed = await _prepare(job_run, given)
    cfg = match_config.current()
    keyring = runtime.keyring
    spotify = await open_spotify(job_run, keyring, given.spotify_connection_id)
    bearer = await open_match_bearer(job_run, cfg, given.swedebeat_connection_id)
    if spotify.account is None or cfg.swedebeat_gateway_key is None:  # both checked already
        raise JobFailure("internal")
    steps: Steps | None = None
    position = given.seed.position if given.seed is not None else None
    content: StudioListContent
    if given.action == "undo" and seed is not None and position is not None:
        content = undone(seed.content, position)
    else:
        model = await group_model(runtime, job_run, given.route_key, "songs")
        steps = Steps(
            job_run,
            model.provider,
            model.agent,
            song_config.current(),
            job_searcher(job_run, keyring, spotify),
            bearer,
            cfg.swedebeat_gateway_key,
            keyring,
            spotify,
        )
        if given.action == "another" and seed is not None and position is not None:
            content = await another(steps, given.brief, seed.content, position)
        else:
            content = await pick(
                steps,
                given.brief,
                swap_anyway=given.action == "swap_anyway",
                playlist_id=given.spotify_playlist_id,
            )
    built = song_list.build(
        content,
        account=spotify.account,
        swedebeat_connection_id=bearer.connection.id,
        generations={"spotify": spotify.generation, "swedebeat": bearer.connection.generation},
        provenance=_provenance(steps, seed),
        parent_id=given.the_seed.song_list_revision_id if seed is not None else None,
    )
    await job_run.stage("working_copy")
    revision_id = uuid.uuid4()
    async with job_run.fenced() as fs:
        class_id = (await fs.db.execute(_JOB_CLASS, fs.lease.params())).scalar_one()
        working = await sync.intend(
            fs.db, fs.lease.owner_id, class_id, revision_id, "working_copy", built.track_ids
        )

    async def write() -> None:
        await write_pool(
            job_run, keyring, spotify, built.track_ids, recheck=sync.recheck(job_run, working)
        )

    await sync.run_sync(job_run, working, write, requeue_busy=False)

    async def store(fs: FencedSession) -> tuple[Any, bytes]:
        if not await hold(fs.db, fs.lease.ctx, spotify) or not await hold(
            fs.db, fs.lease.ctx, bearer.connection
        ):
            raise JobFailure("not_connected")
        await music_kind.check_stored(fs, soundless=False, studio=True)
        return await built.store(fs, revision_id), built.content_hash

    return AwaitApproval(store)


STUDIO_SONGS: Final = KINDS.register(
    JobKind(
        KIND,
        StudioSongsInput,
        STAGES,
        with_plan_run(run),
        consumes_credit=True,
        admit=with_plan(admit),
        approve_check=approve_check,
        server_fields=frozenset({"route_key", "plan_gates"}),
    )
)
