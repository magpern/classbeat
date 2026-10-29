"""The music step of ``design_draft`` (ADR 0006 section 3, step 1): the user's MOWL playlist for
the chosen Spotify playlist, then its analysis with read-only calls. Runs before any model call.

1. **The playlist.** The job's ``mowl_connection_id`` must be the context's MOWL connection
   (``not_connected`` otherwise, before any call). The stored ``mowl_playlist_links`` row, or the
   attach as the external operation ``mowl_attach_spotify_playlist``
   (``classbeat.operations.attach_spotify_playlist``, which stores the link).
2. **The connection guard** (#117 item 1). The link was read without the connection's lock, so a
   reconnect in place as another MOWL account right after it would leave the job with the old
   account's playlist id. The connection is opened, then one fenced transaction share-locks it
   (``classbeat.connections.hold``: the same row version, still ``connected``) and reads the link
   again. A reconnect deletes the links under the connection's row lock, so if both hold, the id
   belongs to the account whose credentials were opened; otherwise ``not_connected`` and no read
   is made. The version check matters on its own: after a reconnect in place the new account may
   have attached the same playlist again, so a link exists, but with its playlist id.
   ``MusicStep.still_current`` repeats the check inside the transaction that stores the revision.
3. **The analysis**: ``mowl.library.spotify_analysis`` (tracks, availability, one read of Spotify's
   sections per track), plus one read of MOWL's playlist list for the playlist's name, all through
   ``classbeat.connections.call_opened`` in a worker thread. While MOWL still reports a BPM of 0
   the library reads the tracks again every ``BPM_POLL_SEC`` (3 s) for up to ``BPM_WAIT_SEC``
   (120 s).

**Checkpoints and the budget.** Every library read is preceded by a checkpoint (ADR 0005 section 4).
The library is synchronous and runs in a thread, so the checkpoint it calls checks the run's flags
at once (lost lease, shutdown) and then runs ``JobRun.checkpoint`` (the fence: lease and cancel) on
the event loop. The step has a time budget of ``MUSIC_BUDGET_SECONDS`` (the BPM wait plus two
minutes of reads); the lease is extended to cover it before the first read (the heartbeat keeps
extending it too), and a checkpoint past the budget stops the analysis with the retryable
``upstream_unavailable``; a read that never ends (bytes trickling in, which urllib's per-operation
timeout does not bound) is cut off by waiting at most the budget plus ``READ_MARGIN_SECONDS`` for
the thread, which then stops at its next checkpoint. A stop (cancel, lost lease, shutdown, budget)
raised inside the thread stops the library at once, with no further read; it travels out of the
connection wrapper as ``_Halt`` (a ``BaseException``, so the wrapper neither turns it into a
``ConnectionFailure`` nor records it as the connection's ``last_error``) and is raised again as
itself here. ``abandoned`` is set when the task stops waiting for the thread, so the thread also
stops at its next checkpoint when the task was cancelled for a reason the other checks do not see;
in the other cases (budget, lost lease, shutdown) those checks already stop it. Defence in depth.

**Never stored as shared.** M3a has no shared analysis store (ADR 0005 section 1; #71 adds it): the
analysis lives in memory for this run only, so every job analyses again. When the store comes, an
analysis with tracks still lacking a BPM (``spotify.unknown_bpm``) must not go in, because their
bars are a guess at 120 BPM; ``shareable`` is that rule. The user is told which tracks have no BPM
yet through the revision (``MusicStep.unknown_bpm``, ``classbeat.design.revision``).

Credentials stay in the ``OpenConnection`` of this step; ``close`` drops it when the run ends.
They never reach a job row, an event, model input or a log. Only counts are logged.

Never nest ``JobRun.fenced()`` or ``checkpoint()`` (#109 item 7): every transaction here is
opened and closed on its own, and the analysis runs while none is open.
"""

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from mowl import api as mowl_api
from mowl import library, spotify
from mowl.errors import MowlError
from sqlalchemy import text

from classbeat.agent.model_input import MusicData
from classbeat.agent.schemas import clean_text
from classbeat.connections import Credentials, OpenConnection, call_opened, hold, open_connection
from classbeat.crypto.keks import KeyRing
from classbeat.jobs import FencedSession, JobFailure, JobRun, RunStopped
from classbeat.jobs.fence import CALL_LEASE_MARGIN_SECONDS
from classbeat.operations import attach_spotify_playlist
from classbeat.operations.attach import SOURCE, check_source_id

logger = logging.getLogger(__name__)

# The BPM wait (``library.BPM_WAIT_SEC``) plus two minutes for the other reads.
MUSIC_BUDGET_SECONDS: Final = float(library.BPM_WAIT_SEC + 120)
BUDGET_CATEGORY: Final = "upstream_unavailable"  # MOWL too slow: retried with backoff
CHECKPOINT_TIMEOUT_SECONDS: Final = 30.0  # one fenced checkpoint from the library's thread
# The task waits for the thread at most the budget plus this (a read urllib's per-operation
# timeout never ends, bytes trickling in): then the step ends and the thread stops at its next
# checkpoint.
READ_MARGIN_SECONDS: Final = 30.0
MAX_TITLE: Final = 200

_LINK = text(
    "SELECT id, playlist_id FROM mowl_playlist_links "
    "WHERE owner_id = :owner_id AND source = :source AND source_id = :source_id"
)
_LINK_BY_ID = text(
    "SELECT playlist_id FROM mowl_playlist_links "
    "WHERE id = :id AND owner_id = :owner_id AND source = :source AND source_id = :source_id"
)


class _Halt(BaseException):
    """A stop raised by the checkpoint inside the library's thread; ``cause`` is raised again
    once out of the connection wrapper."""

    def __init__(self, cause: Exception) -> None:
        super().__init__("halted")
        self.cause = cause


@dataclass(frozen=True, slots=True)
class UnknownBpm:
    index: int  # the track's index in the music summary (0-based)
    title: str


@dataclass(eq=False)
class MusicStep:
    """The analysed playlist for this run and how it was reached."""

    data: MusicData
    source_id: str
    link_id: UUID
    fingerprint: str
    unknown_bpm: tuple[UnknownBpm, ...]
    _opened: OpenConnection | None

    async def still_current(self, fs: FencedSession) -> None:
        """In the transaction that stores the revision: the connection is still the one the
        music was read with (``hold``: same version, ``connected``, share-locked until the
        commit), and the link still exists with the same playlist (a reconnect or disconnect
        meanwhile deleted it). ``not_connected`` otherwise, and nothing is stored."""
        opened = self._opened
        if opened is None:
            raise JobFailure("not_connected")
        if not await hold(fs.db, fs.lease.ctx, opened):
            raise JobFailure("not_connected")
        row = (
            await fs.db.execute(
                _LINK_BY_ID,
                {
                    "id": self.link_id,
                    "owner_id": fs.lease.owner_id,
                    "source": SOURCE,
                    "source_id": self.source_id,
                },
            )
        ).first()
        if row is None or row.playlist_id != self.data.playlist_id:  # defence in depth, as above
            raise JobFailure("not_connected")

    def close(self) -> None:
        """Drop the credentials (the run is over)."""
        self._opened = None


def shareable(analysis: Mapping[str, Any]) -> bool:
    """Whether an analysis may be kept as the shared one (#71): not while a track's BPM is
    unknown, since its bars are a guess. M3a keeps none."""
    return not spotify.unknown_bpm(dict(analysis["map"]))


def fingerprint(m: Mapping[str, Any]) -> str:
    """SHA-256 of the analysed track list: per track in play order its Spotify track id and
    length (the library's map keeps no MOWL track ids), so a later revision can tell whether
    the music changed."""
    tracks = [[str(t["spotify_id"]), round(float(t["sec"]), 3)] for t in m["tracks"]]
    canonical = json.dumps(tracks, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def unknown_bpm(m: Mapping[str, Any]) -> tuple[UnknownBpm, ...]:
    """The tracks MOWL had no BPM for, by index (``spotify.unknown_bpm`` names them)."""
    return tuple(
        UnknownBpm(int(t["track"]) - 1, clean_text(str(t.get("title") or ""))[:MAX_TITLE])
        for t in m["tracks"]
        if not t.get("bpm")
    )


def _thread_checkpoint(
    run: JobRun,
    loop: asyncio.AbstractEventLoop,
    deadline: float,
    clock: Callable[[], float],
    abandoned: threading.Event | None = None,
) -> Callable[[], None]:
    """The checkpoint the library calls (in its thread) before each read. ``abandoned`` is set
    when the task stopped waiting for the thread (defence in depth: the deadline, ``lost`` and
    ``shutdown`` stop it in the usual cases)."""

    def check() -> None:
        if abandoned is not None and abandoned.is_set():
            raise _Halt(JobFailure(BUDGET_CATEGORY))
        if run.lost:
            raise _Halt(RunStopped("lost"))
        if run.shutdown.is_set():
            raise _Halt(RunStopped("shutdown"))
        if clock() > deadline:
            raise _Halt(JobFailure(BUDGET_CATEGORY))
        try:
            future = asyncio.run_coroutine_threadsafe(run.checkpoint(), loop)
        except Exception as exc:  # the loop is gone: the worker stopped
            raise _Halt(exc) from None
        try:
            future.result(CHECKPOINT_TIMEOUT_SECONDS)
        except Exception as exc:  # RunStopped, a database error, or no answer in time
            future.cancel()
            raise _Halt(exc) from None

    return check


def _playlist_name(creds: Credentials, source_id: str) -> str | None:
    """The playlist's name as MOWL lists it (a new playlist may be missing from the list)."""
    try:
        playlists = mowl_api.spotify_playlists(creds)
    except MowlError:
        return None
    for p in playlists if isinstance(playlists, list) else []:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(p, dict) and p.get("ID") == source_id:  # pyright: ignore[reportUnknownMemberType]
            name: object = p.get("Name")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            return name if isinstance(name, str) else None
    return None


async def _held_link(run: JobRun, opened: OpenConnection, source_id: str) -> tuple[UUID, int]:
    """The link, read under the connection's share lock; the lease extended for the budget."""
    async with run.fenced() as fs:
        if not await hold(fs.db, run.ctx, opened):
            raise JobFailure("not_connected")
        row = (
            await fs.db.execute(
                _LINK,
                {"owner_id": run.lease.owner_id, "source": SOURCE, "source_id": source_id},
            )
        ).first()
        if row is None:  # defence in depth: hold passes unless a path changed only the link
            raise JobFailure("not_connected")
        await fs.extend_lease(MUSIC_BUDGET_SECONDS + CALL_LEASE_MARGIN_SECONDS)
        return row.id, int(row.playlist_id)


async def _analyse(
    run: JobRun,
    opened: OpenConnection,
    playlist_id: int,
    source_id: str,
    clock: Callable[[], float],
) -> tuple[list[str], dict[str, Any]]:
    abandoned = threading.Event()
    budget = MUSIC_BUDGET_SECONDS
    check = _thread_checkpoint(run, asyncio.get_running_loop(), clock() + budget, clock, abandoned)

    def read(creds: Credentials) -> tuple[list[str], dict[str, Any] | None]:
        check()
        name = _playlist_name(creds, source_id)
        return library.spotify_analysis(creds, playlist_id, source_id, name, checkpoint=check)

    try:
        unavailable, analysis = await asyncio.wait_for(
            call_opened(run.factory, run.ctx, opened, read), budget + READ_MARGIN_SECONDS
        )
    except _Halt as halt:
        raise halt.cause from None
    except TimeoutError:  # a read that never ends: the thread stops at its next checkpoint
        raise JobFailure(BUDGET_CATEGORY) from None
    finally:
        abandoned.set()
    if analysis is None or not analysis["map"]["tracks"]:
        raise JobFailure("invalid_input")  # nothing playable in the playlist
    return unavailable, analysis


async def load_music(
    run: JobRun,
    keyring: KeyRing,
    source_id: str,
    *,
    connection_id: UUID,
    clock: Callable[[], float] = time.monotonic,
) -> MusicStep:
    """The music step; raises ``JobFailure`` with the category, or ``RunStopped``."""
    source_id = check_source_id(source_id)
    if run.ctx.connection("mowl") != connection_id:
        raise JobFailure("not_connected")
    await attach_spotify_playlist(run, keyring, source_id, connection_id=connection_id)
    await run.checkpoint()
    opened = await open_connection(run.factory, keyring, run.ctx, "mowl")
    if opened.id != connection_id:
        raise JobFailure("not_connected")
    link_id, playlist_id = await _held_link(run, opened, source_id)
    unavailable, analysis = await _analyse(run, opened, playlist_id, source_id, clock)
    m = analysis["map"]
    step = MusicStep(
        data=MusicData(analysis, playlist_id, tuple(unavailable)),
        source_id=source_id,
        link_id=link_id,
        fingerprint=fingerprint(m),
        unknown_bpm=unknown_bpm(m),
        _opened=opened,
    )
    logger.info(
        "music analysed",
        extra={
            "job_id": str(run.lease.job_id),
            "tracks": len(m["tracks"]),
            "unknown_bpm": len(step.unknown_bpm),
        },
    )
    return step
