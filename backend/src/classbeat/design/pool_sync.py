"""The pool's sync wait of the music step (``design.music``, ADR 0008 section 2; #1535).

Before analysing a playlist classbeat just wrote, the music step reads MOWL's track list of the
wrapper until its Spotify ids are exactly the ids written (the same ids, each as often; MOWL's
order is not relied on), every ``SYNC_POLL_SECONDS``, for at most the wait it is given, with a
checkpoint before each read. A list that does not match in time ends the step
``pool_not_synced``. With the fresh wrapper of each write (``operations.attach_written_playlist``)
the first read should match; the wait stays as a guard (MOWL may still be indexing a new wrapper).

On a timeout only counts are logged (expected, listed and overlapping ids of the last read),
never an id, so the next mismatch can be told apart without a live MOWL read.

Runs in the library's worker thread (``design.music._analyse``); a stop travels out as ``Halt``.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Final

from mowl import api as mowl_api
from mowl import library

from classbeat.connections import Credentials
from classbeat.jobs import JobFailure

logger = logging.getLogger(__name__)

SYNC_POLL_SECONDS: Final = float(library.BPM_POLL_SEC)  # the pool's sync wait reads this often
NOT_SYNCED: Final = "pool_not_synced"


class Halt(BaseException):
    """A stop raised by the checkpoint inside the library's thread; ``cause`` is raised again
    once out of the connection wrapper (``design.music``)."""

    def __init__(self, cause: Exception) -> None:
        super().__init__("halted")
        self.cause = cause


def listed_ids(tracks: object) -> list[str] | None:
    """The Spotify ids of MOWL's track list, sorted (None when it is not a list of tracks)."""
    if not isinstance(tracks, list):
        return None
    ids: list[str] = []
    for track in tracks:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(track, dict):
            return None
        ids.append(str(track.get("SpotifyTrackID")))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return sorted(ids)


def counts(wanted: Sequence[str], listed: Sequence[str] | None) -> dict[str, int]:
    """What a timeout logs: how many ids were expected, listed and in both (as often)."""
    got = list(listed or ())
    overlap = sum((Counter(wanted) & Counter(got)).values())
    return {"expected": len(wanted), "listed": len(got), "overlap": overlap}


def wait_synced(
    creds: Credentials,
    playlist_id: int,
    expect: Sequence[str],
    wait_seconds: float,
    check: Callable[[], None],
    clock: Callable[[], float],
    job_id: str = "",
) -> None:
    """Until MOWL lists exactly ``expect``; ``pool_not_synced`` after ``wait_seconds`` (module
    docstring). Every read is preceded by ``check``."""
    wanted = sorted(expect)
    deadline = clock() + wait_seconds
    while True:
        check()
        listed = listed_ids(mowl_api.playlist_tracks(creds, playlist_id))
        if listed == wanted:
            return
        left = deadline - clock()
        if left <= 0:
            logger.warning("pool not synced", extra={"job_id": job_id, **counts(wanted, listed)})
            raise Halt(JobFailure(NOT_SYNCED))
        time.sleep(min(SYNC_POLL_SECONDS, left))
