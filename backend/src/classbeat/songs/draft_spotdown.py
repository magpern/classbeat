"""spotdown's stages A, B and D for a ``design_draft`` on the user's MOWL playlist (ADR 0040
amendment "spotdown is the only analysis path", items 3 to 5; #1407), in the ``music`` stage,
after MOWL's analysis of the playlist (``music.load_music``) and before the beat-one corrections
and the design (``design.kind_music_step``).

MOWL's map is the **frame** (track order, MOWL's length, Spotify's BPM) and its sections are
**anchors** only (item 3); no track is ever designed on MOWL's sections:

1. ``draft_list_policy.whole_list``: every track of the map, in order (a track spotdown cannot
   align is ``analysis_failed``; more than a revision records is ``invalid_input``);
2. stages A and B as for the user's own playlist (``playlist_spotdown``: no spares, no rounds; a
   song that is missing is ``analysis_failed``, an unsuitable one a warning);
   The record is the first thing that holds its size: one over ``MAX_RECORD_BYTES`` ends the job
   ``invalid_input`` here, before stage D and before anything is stored (#1438);
3. stage D on that record (``phase2_spotdown.listed_music``): every track read again by its
   analysis id, verified against the track and rebuilt on spotdown's sections, then stamped.

The record is kept in the draft itself (``music.spotdown.record``,
``revision_spotdown_model``): the playlist has no ``song_list`` revision. A revise or refit reads
the same analyses again (``songs.spotdown_carry``).

**Outcomes** (``spotdown.outage_policy``, item 4): spotdown unreachable (its fixed line logged,
``outage_policy.log_message``), a provider outage, the budget spent or a ``rate_limited`` read ->
``analysis_unavailable``; a song spotdown cannot match or time -> ``analysis_failed``; any other
error is not swallowed. Only counts are logged: never a title, an id, a source or a key.
"""

import logging

from classbeat.design.music import MusicStep
from classbeat.design.revision_spotdown_model import SpotdownSections, fits
from classbeat.jobs import JobFailure, JobRun
from classbeat.songs.draft_list_policy import whole_list
from classbeat.songs.phase2_spotdown import listed_music
from classbeat.songs.playlist_spotdown import playlist_spotdown

logger = logging.getLogger(__name__)


async def drafted_music(run: JobRun, step: MusicStep) -> tuple[MusicStep, SpotdownSections]:
    """``step`` with every track on spotdown's sections, and the record the draft keeps (module
    docstring), or ``JobFailure``. The caller closes ``step`` when this raises."""
    analysis_map = step.data.analysis["map"]
    chosen = whole_list(analysis_map)
    if isinstance(chosen, str):
        raise JobFailure(chosen)
    records = await playlist_spotdown(run, chosen, analysis_map)  # stages A and B
    if not fits(records):
        raise JobFailure("invalid_input")  # over its bound: refused before stage D, never stored
    ids = [s.spotify_id for s in chosen.songs]
    timing = SpotdownSections(record=records, tracks=list(range(len(ids))))
    step = await listed_music(run, ids, records, step)  # stage D
    logger.info(
        "spotdown playlist design", extra={"job_id": str(run.lease.job_id), "songs": len(ids)}
    )
    return step, timing
