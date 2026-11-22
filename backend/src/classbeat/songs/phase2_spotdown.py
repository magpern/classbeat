"""spotdown's sections in a song pick's phase 2 (ADR 0040 amendment "spotdown is the only analysis
path", items 3 and 4, and its SD-P6 stage D amendment; plan sections 1 step 6 and 6; #1303, #1354):
the music the design gets, after the final playlist's MOWL analysis was checked
(``phase2._music``) and before the design stage.

The match (stage A) and the analysis with its refinement (stage B) ran in phase 1, before the list
was stored and approved; this stage calls neither again (the approved list is fixed) and reads only
what stage B recorded in the list's provenance (``spotdown_analysis``).

For **every song** of the list, in order:

1. has a recorded entry (``song_sections_calc.recorded``): a song without one is
   ``analysis_failed`` (a designed track always has a stamp);
2. ``GET /v2/analyses/{id}`` of the recorded analysis (global: no ``cache_scope``), after a
   checkpoint (a cancel, a shutdown or a lost lease stops the run);
3. the document must be that analysis, aligned to this track (``song_sections_calc.verified``),
   map on the Spotify clock (``spotdown_map_policy.reference_sections``) and be ``approximate``
   (``song_sections_calc.apply_track``); then the track's sections are spotdown's and the track is
   stamped (``track_timing_calc.stamp``), which makes the design validator (``timing_gate``) and
   the model's ``timing`` key (``timing_input``) apply to it.

**Outcomes** (item 4; ``spotdown.outage_policy``): an unreachable spotdown (the category of its
reason, its fixed line logged), or a ``rate_limited`` read (phase 2 never waits) ->
``analysis_unavailable``; a recorded analysis that is gone, or does not verify, map or stay
``approximate`` -> ``analysis_failed``. There is no fallback to MOWL's sections and no error is
swallowed. **Always strict** (#1386): a list without the record, or with a song it does not
mark, is ``analysis_failed``; every list that reaches a design runs stages A and B first (a pick,
``pick``; an own playlist, ``playlist_spotdown``; the composer, ``composer_spotdown``). Only
counts and codes are logged: never a title, an id, a source or a key.

``listed_music`` is the same stage for the songs of a list that is not stored yet: the composer's
preview design (``composer_design.songs_music``), on its list's provenance.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from classbeat.agent.model_input import MusicData
from classbeat.design.music import MusicStep
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs.pick_unavailable import failure_of
from classbeat.songs.song_list import SongListRevision
from classbeat.spotdown import outage_policy, song_match_config
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import TrackFailure, Unreachable
from classbeat.spotdown.provenance_read_calc import source_kind
from classbeat.spotdown.song_sections_calc import Recorded, apply_track, recorded, verified
from classbeat.spotdown.spotdown_map_policy import MapRefused, reference_sections

logger = logging.getLogger(__name__)


async def _track(
    client: SpotdownClient, rec: Recorded, m: Mapping[str, Any], spotify_id: str
) -> dict[str, Any]:
    """``m`` with this song on spotdown's sections; ``JobFailure`` otherwise."""
    doc = await client.get_analysis(rec.analysis_id)  # a youtube analysis is global: no scope
    if isinstance(doc, Unreachable):
        raise failure_of(doc)
    if isinstance(doc, TrackFailure):
        if doc.status == "rate_limited":
            raise JobFailure(outage_policy.UNAVAILABLE)  # no wait in phase 2
        raise JobFailure(outage_policy.FAILED)  # the recorded analysis is gone
    track = m["tracks"][rec.position]
    if str(track.get("spotify_id")) != spotify_id:
        raise JobFailure(outage_policy.INTERNAL)  # ``phase2.check_music`` checked the order
    if not verified(doc, rec, spotify_id, float(track["sec"])):
        raise JobFailure(outage_policy.FAILED)
    mapped = reference_sections(doc, rec.decision)
    kind = source_kind(doc)
    if isinstance(mapped, MapRefused) or kind is None:
        raise JobFailure(outage_policy.FAILED)
    made = apply_track(m, rec.position, rec.decision, kind, mapped.sections)
    if made is None:  # not ``approximate`` as the stamp reads it
        raise JobFailure(outage_policy.FAILED)
    return made


async def _with_spotdown(
    run: JobRun, ids: Sequence[str], provenance: Mapping[str, Any], analysis: Mapping[str, Any]
) -> dict[str, Any]:
    """The analysis with spotdown's sections for every song of the list."""
    by_position = {r.position: r for r in recorded(provenance)}
    client = SpotdownClient(song_match_config.current().sources)
    m = analysis["map"]
    for position, spotify_id in enumerate(ids):
        rec = by_position.get(position)
        if rec is None:
            raise JobFailure(outage_policy.FAILED)  # a designed track always has a stamp
        await run.checkpoint()
        m = await _track(client, rec, m, spotify_id)
    return {**analysis, "map": m}


async def listed_music(
    run: JobRun, ids: Sequence[str], provenance: Mapping[str, Any], step: MusicStep
) -> MusicStep:
    """``step`` on spotdown's sections for every song ``ids`` lists (in order), from the record in
    ``provenance``, or ``JobFailure`` (module docstring). The caller closes ``step`` when this
    raises. A list with no song is ``internal``: every designed list has one."""
    if not ids:
        raise JobFailure(outage_policy.INTERNAL)
    analysis = await _with_spotdown(run, ids, provenance, step.data.analysis)
    logger.info(
        "spotdown song sections", extra={"job_id": str(run.lease.job_id), "songs": len(ids)}
    )
    step.data = MusicData(analysis, step.data.playlist_id, step.data.unavailable)
    return step


async def spotdown_music(run: JobRun, stored: SongListRevision, step: MusicStep) -> MusicStep:
    """``step`` on spotdown's sections for every song of the stored list (``listed_music``)."""
    return await listed_music(run, stored.track_ids, stored.provenance, step)
