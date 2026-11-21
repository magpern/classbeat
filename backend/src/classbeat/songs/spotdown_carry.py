"""spotdown's sections carried into a ``revise_draft`` or ``refit_draft`` of a Spotify design (ADR
0040, its SD-P6 stage E amendment; #1303), in the music step (``design.kind_music_step``), before
the class's beat-one corrections are attached.

The base recorded the tracks it had on spotdown's sections and the ``song_list`` revision whose
record gave them (``music.spotdown``, read by ``design.spotdown_base``). Here:

1. that list is read through the job's own class (``song_list.load``, its hash checked again);
2. the music MOWL analysed now must be exactly that list's tracks, in order;
3. ``phase2_spotdown.spotdown_music`` for **those tracks only** (``only``): each analysis read
   again by its id and verified, exactly as stage D does in phase 2. A track the base had on
   MOWL's sections stays on them, so a revise never changes a track's timing the base did not
   have; a track whose read fails keeps MOWL's sections alone.

**Fallback to today's music** (the step as it was, byte for byte): the list gone or not
loading, other music than the list's, and everything ``spotdown_music`` falls back on (off or
not allowlisted, spotdown unreachable, an error of any type there); an error of any type here is
swallowed (only its type is logged). Only ``RunStopped``, ``LeaseLost`` and ``DatabaseRoleError``
pass.
"""

import logging

from classbeat.design.music import MusicStep
from classbeat.design.revision_spotdown_model import SpotdownSections
from classbeat.jobs import JobRun
from classbeat.songs import song_list
from classbeat.songs.phase2_spotdown import spotdown_music
from classbeat.songs.pick_match import PASSED
from classbeat.songs.seed import class_of
from classbeat.songs.song_list import SongListRevision

logger = logging.getLogger(__name__)


async def _list(
    run: JobRun, sections: SpotdownSections, step: MusicStep
) -> SongListRevision | None:
    async with run.fenced() as fs:
        stored = await song_list.load(
            fs.db, fs.lease.owner_id, await class_of(fs), sections.song_list_id
        )
    if stored is None:
        return None
    tracks = step.data.analysis["map"]["tracks"]
    if tuple(str(t.get("spotify_id")) for t in tracks) != stored.track_ids:
        return None  # not the list's music (a playlist changed since): today's path
    return stored


async def carried_music(run: JobRun, step: MusicStep, sections: SpotdownSections) -> MusicStep:
    """``step`` with the base's spotdown tracks read again, or ``step`` unchanged (module
    docstring). The caller closes ``step`` when this raises."""
    try:
        stored = await _list(run, sections, step)
        if stored is None:
            logger.info(
                "spotdown carried sections fallback",
                extra={"job_id": str(run.lease.job_id), "reason": "not_the_list"},
            )
            return step
        return await spotdown_music(run, stored, step, only=frozenset(sections.tracks))
    except PASSED:
        raise
    except Exception as exc:
        kind = type(exc).__name__
        logger.warning("spotdown carried sections failed", extra={"error_type": kind})
        return step
