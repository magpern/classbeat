"""spotdown's sections carried into a ``revise_draft`` or ``refit_draft`` of a Spotify design (ADR
0040 amendment "spotdown is the only analysis path", item 4, and its SD-P6 stage E amendment; #1303,
#1354), in the music step (``design.kind_music_step``), before the class's beat-one corrections are
attached.

The base recorded the tracks it had on spotdown's sections and where their stage-B record lives
(``music.spotdown``, read by ``design.spotdown_base``): a ``song_list`` revision, or, for a
``design_draft`` on the user's MOWL playlist, the record itself (#1407). Here:

1. a list is read through the job's own class (``song_list.load``, its hash checked again) and
   the music MOWL analysed now must be exactly its tracks, in order; for a record, the music's
   fingerprint must be the base's (``fingerprint``: the same tracks and lengths, in order);
2. the base must have had **every** track on spotdown's sections;
3. ``phase2_spotdown.listed_music``: each analysis read again by its id and verified, exactly as
   stage D does in phase 2.

**Outcomes**: the list gone or not loading, other music than the list's or the base's, or a base
that had only some tracks on spotdown's sections: ``music_changed`` (the music is not the one the
draft was designed on; there is no fallback to MOWL's sections). Everything stage D ends in
(``analysis_unavailable`` for an unreachable spotdown or a ``rate_limited`` read,
``analysis_failed`` for an analysis that is gone or no longer verifies) passes; nothing is
swallowed.
"""

from uuid import UUID

from classbeat.design.music import MusicStep
from classbeat.design.revision_spotdown_model import SpotdownSections
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs import song_list
from classbeat.songs.phase2_spotdown import listed_music, spotdown_music
from classbeat.songs.seed import class_of
from classbeat.songs.song_list import SongListRevision


async def _list(
    run: JobRun, list_id: UUID, sections: SpotdownSections, step: MusicStep
) -> SongListRevision:
    async with run.fenced() as fs:
        stored = await song_list.load(fs.db, fs.lease.owner_id, await class_of(fs), list_id)
    if stored is None:
        raise JobFailure("music_changed")  # the list is gone
    tracks = step.data.analysis["map"]["tracks"]
    if tuple(str(t.get("spotify_id")) for t in tracks) != stored.track_ids:
        raise JobFailure("music_changed")  # not the list's music (a playlist changed since)
    if sections.tracks != list(range(len(stored.track_ids))):
        raise JobFailure("music_changed")  # the base had only some tracks on spotdown
    return stored


async def _recorded(
    run: JobRun, sections: SpotdownSections, step: MusicStep, fingerprint: str | None
) -> MusicStep:
    """A playlist design's own record (#1407): the base's music again, every track re-read."""
    if sections.record is None or fingerprint is None:
        raise JobFailure("internal")  # a record carry always has the base's fingerprint
    if step.fingerprint != fingerprint:
        raise JobFailure("music_changed")  # the playlist changed since the base was designed
    ids = [str(t.get("spotify_id")) for t in step.data.analysis["map"]["tracks"]]
    if sections.tracks != list(range(len(ids))):
        raise JobFailure("music_changed")  # the base had only some tracks on spotdown
    return await listed_music(run, ids, sections.record, step)


async def carried_music(
    run: JobRun, step: MusicStep, sections: SpotdownSections, fingerprint: str | None = None
) -> MusicStep:
    """``step`` with the base's spotdown tracks read again (module docstring); ``fingerprint``:
    the base's music. The caller closes ``step`` when this raises."""
    if sections.song_list_id is None:
        return await _recorded(run, sections, step, fingerprint)
    return await spotdown_music(run, await _list(run, sections.song_list_id, sections, step), step)
