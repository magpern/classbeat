"""spotdown's sections in a song pick's phase 2 (ADR 0040 sections 2, 3 and 8 and its SD-P6 stage D
amendment; plan sections 1 step 6 and 6; #1303): the music the design gets, after the final
playlist's MOWL analysis was checked (``phase2._music``) and before the design stage.

The match (stage A) and the analysis with its refinement (stage B) ran in phase 1, before the list
was stored and approved; this stage calls neither again (the approved list is fixed) and reads only
what stage B recorded in the list's provenance (``spotdown_analysis``).

**Inactive** (``song_match_config``: off by default, an allowlisted owner only, no value turns it
on for everyone), or a list whose record marks no song: nothing is called and the step is given
back as it is, so the design gets **exactly today's music**.

**Active**, for each song the record marks (``song_sections_calc.recorded``), in list order:

1. ``GET /v2/analyses/{id}`` of the recorded analysis (global: no ``cache_scope``), after a
   checkpoint (a cancel, a shutdown or a lost lease stops the run);
2. the document must be that analysis, aligned to this track (``song_sections_calc.verified``),
   map on the Spotify clock (``spotdown_map_policy.reference_sections``) and be ``approximate``
   (``song_sections_calc.apply_track``); then the track's sections are spotdown's and the track is
   stamped (``track_timing_calc.stamp``), which makes the design validator (``timing_gate``) and
   the model's ``timing`` key (``timing_input``) apply to it;
3. a song whose document is refused, not verified or not ``approximate`` (``unusable``, an
   unknown class), or whose read is ``rate_limited`` (phase 2 never waits), keeps **MOWL's
   sections** and no stamp: only that track differs from today.

**Fallback to today's music** for the whole class (the step as it was, byte for byte): spotdown
unreachable on any call (``client.get_analysis``: every answer but ``200`` and ``429``, so also a
``5xx`` outage and an analysis that is gone), or **an error of any type** in here (swallowed;
only its type is logged). Only ``RunStopped``,
``LeaseLost`` and ``DatabaseRoleError`` pass. Only counts and codes are logged: never a title, an
id, a source or a key.
"""

import logging
from collections.abc import Mapping
from typing import Any

from classbeat.agent.model_input import MusicData
from classbeat.design.music import MusicStep
from classbeat.jobs import JobRun
from classbeat.songs.pick_match import PASSED
from classbeat.songs.pick_match_batch import Fallback
from classbeat.songs.song_list import SongListRevision
from classbeat.spotdown import song_match_config
from classbeat.spotdown.client import SpotdownClient
from classbeat.spotdown.failure_policy import TrackFailure, Unreachable
from classbeat.spotdown.provenance_read_calc import source_kind
from classbeat.spotdown.song_sections_calc import Recorded, apply_track, recorded, verified
from classbeat.spotdown.spotdown_map_policy import MapRefused, reference_sections

logger = logging.getLogger(__name__)


async def _track(
    client: SpotdownClient, rec: Recorded, m: Mapping[str, Any], spotify_id: str
) -> dict[str, Any] | None:
    """``m`` with this song on spotdown's sections, or ``None`` (MOWL's sections)."""
    doc = await client.get_analysis(rec.analysis_id)  # a youtube analysis is global: no scope
    if isinstance(doc, Unreachable):
        raise Fallback("unreachable")
    if isinstance(doc, TrackFailure):
        return None  # ``rate_limited``: no wait in phase 2
    track = m["tracks"][rec.position]
    if str(track.get("spotify_id")) != spotify_id:
        return None  # defence in depth: ``phase2.check_music`` checked the order
    if not verified(doc, rec, spotify_id, float(track["sec"])):
        return None
    mapped = reference_sections(doc, rec.decision)
    if isinstance(mapped, MapRefused):
        return None
    kind = source_kind(doc)
    if kind is None:
        return None
    return apply_track(m, rec.position, rec.decision, kind, mapped.sections)


async def _with_spotdown(
    run: JobRun,
    stored: SongListRevision,
    analysis: Mapping[str, Any],
    only: frozenset[int] | None,
) -> tuple[dict[str, Any] | None, int, int]:
    """The analysis with spotdown's sections (``None``: nothing applied), the marked and the
    applied counts."""
    config = song_match_config.current()
    if not config.active_for(run.lease.owner_id):
        return None, 0, 0
    marked = [r for r in recorded(stored.provenance) if only is None or r.position in only]
    if not marked:
        return None, 0, 0
    client = SpotdownClient(config.sources)
    ids, m = stored.track_ids, analysis["map"]
    applied = 0
    for rec in marked:
        if not 0 <= rec.position < len(ids):
            continue  # not a song of this list: there is none to change
        await run.checkpoint()
        made = await _track(client, rec, m, ids[rec.position])
        if made is not None:
            m, applied = made, applied + 1
    return (None if not applied else {**analysis, "map": m}), len(marked), applied


async def spotdown_music(
    run: JobRun, stored: SongListRevision, step: MusicStep, only: frozenset[int] | None = None
) -> MusicStep:
    """``step`` on spotdown's sections for the marked songs (``only``: of those, the positions a
    revised draft had on them, ``spotdown_carry``), or ``step`` unchanged (inactive, nothing
    applied, or any fallback). The caller closes ``step`` when this raises."""
    try:
        analysis, marked, applied = await _with_spotdown(run, stored, step.data.analysis, only)
    except PASSED:
        raise
    except Exception as exc:
        reason = exc.reason if isinstance(exc, Fallback) else "internal"
        if not isinstance(exc, Fallback):
            kind = type(exc).__name__
            logger.warning("spotdown song sections failed", extra={"error_type": kind})
        logger.info(
            "spotdown song sections fallback",
            extra={"job_id": str(run.lease.job_id), "reason": reason},
        )
        return step
    if marked:
        logger.info(
            "spotdown song sections",
            extra={"job_id": str(run.lease.job_id), "marked": marked, "applied": applied},
        )
    if analysis is not None:
        step.data = MusicData(analysis, step.data.playlist_id, step.data.unavailable)
    return step
