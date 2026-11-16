"""The ``spotify_sync`` job kind (ADR 0036 section 9, SR C8): one "Sync again" of a revision's
Spotify playlist, enqueued only by the studio's route (``classes.studio_sync``), never by the jobs
API or a model.

Its input ``{source_class_id, revision_id, target}`` is no ``JobInput``, so the jobs API refuses
to create it (``unknown_kind``). The job's ``class_id`` is NULL (migration 0056): a sync runs
beside the class's own open job (a list waiting for its approval, an output job). The class is
named by its input and re-read here: a class or row that is gone, or a row that is no longer
``pending``, ends the job with nothing written.

The write is the one the revision's first sync made (``playlists.sync``): the working copy
through ``playlists.write_pool``, a final playlist through ``fill_final_playlist`` to its
``class_playlists`` row under the studio output's own fill key (the operation protocol takes a
``failed`` row over, reconciles an ``uncertain`` one first and reuses a ``succeeded`` one; the
output's state is derived from that operation), each under the playlist's lock with the
intended revision re-checked in its decide step, read back and compared. A failure is the row's
state (``failed``, with its category), not the job's; a lost lease and a busy playlist
(``operation_busy``) requeue it.
"""

from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from classbeat.design.kind import current_runtime
from classbeat.jobs import KINDS, Complete, JobFailure, JobKind, JobRun
from classbeat.playlists import fill_final_playlist, open_spotify, sync, write_pool

KIND: Final = "spotify_sync"
STAGES: Final = ("sync",)


class SpotifySyncInput(BaseModel):
    """Stored by the "Sync again" route: the class, the revision and the playlist to follow it."""

    model_config = ConfigDict(extra="forbid")

    source_class_id: UUID
    revision_id: UUID
    target: Literal["working_copy", "final"]


async def run(job_run: JobRun) -> Complete:
    runtime = current_runtime()
    given = job_run.input
    if runtime is None or not isinstance(given, SpotifySyncInput) or job_run.approved:
        raise JobFailure("internal")
    await job_run.stage("sync")
    async with job_run.fenced() as fs:
        row = await sync.load(
            fs.db, fs.lease.owner_id, given.source_class_id, given.revision_id, given.target
        )
    if row is None or row.state != "pending":
        return Complete()  # gone with its class, or settled by another write meanwhile
    keyring = runtime.keyring
    found = row

    async def write() -> None:
        connection_id = job_run.ctx.connection("spotify")
        if connection_id is None:
            raise JobFailure("not_connected")
        opened = await open_spotify(job_run, keyring, connection_id)
        if found.target == "working_copy":
            await write_pool(
                job_run, keyring, opened, found.track_ids, recheck=sync.recheck(job_run, found)
            )
            return
        if found.class_playlist_id is None or found.fill_key is None:
            raise JobFailure("internal")
        await fill_final_playlist(
            job_run,
            keyring,
            opened,
            class_id=found.class_id,
            class_playlist_id=found.class_playlist_id,
            track_ids=found.track_ids,
            key=found.fill_key,
            recheck=lambda playlist_id, key: sync.final_recheck(job_run, found, playlist_id, key),
        )

    await sync.run_sync(job_run, row, write, requeue_busy=True)
    return Complete()


SPOTIFY_SYNC: Final = KINDS.register(
    JobKind(KIND, SpotifySyncInput, STAGES, run, consumes_credit=False)
)
