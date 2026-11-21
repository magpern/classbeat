"""The analysis stage of a Spotify song pick (ADR 0040 sections 2, 3 and 8, plan sections 1 steps 4
and 5, and 6; SD-P6 stage B, #1303): what each ``POST /v2/analyses`` outcome means for a selected
track, the bounded reactions, and what a slot of the chosen list needs. Pure: no I/O.

**Only the selected tracks** are analysed: a new song of the chosen list whose match is
``timing`` (``song_match_policy``), by its sources (the best, then at most ``MAX_ALTERNATES``
alternates), never the pool. **Every result is at best ``approximate``**: what is played is
Spotify's audio, which classbeat never holds (``timing_class_policy``).

**Reactions** (``react``) to a per-track status, after which the track either asks again or ends:

- ``rate_limited``: ``wait`` its (clamped) ``retry_after_s`` and ask again, not an attempt;
- ``provider_error``: once ``again`` after ``RETRY_PAUSE_SECONDS``, then MOWL's sections
  (``sections``); a batch where at least half (and ``OUTAGE_MIN_TRACKS``) end so is a provider
  outage (``song_match_policy.provider_outage``): no swaps, the whole class keeps MOWL's sections;
- ``processing_failed``: deterministic, so no retry on the same audio except **one after a
  ``timeout``**; then the next ``alternate``, then MOWL's sections;
- ``source_unavailable`` and ``not_found``: the next ``alternate``, then MOWL's sections;
- ``alignment_failed``: the timeline is unusable: MOWL's sections (``alignment_failed``);
- ``uncertain`` (and any other status): MOWL's sections.

At most ``MAX_REQUESTS`` requests per track over all its sources; past them MOWL's sections.
MOWL's sections are today's path for that track: a Spotify class always has them for a chosen
song (``analysed_pool`` lists only tracks MOWL gave a BPM), so the fall-through of section 3
(alternate, MOWL's sections, replacement) ends at MOWL's sections here.

**What a slot needs** (``slot_need``, for ``track_fit_policy.check``): the BPM the song was chosen
with (MOWL's; half or double within ``track_fit_policy.BPM_TOLERANCE``), a length within
``LENGTH_SLACK_S`` of the length it was chosen with, never ``exact`` timing, and for a ``warmup``
a low-energy start.
"""

from dataclasses import dataclass
from typing import Final, Literal

from classbeat.spotdown.failure_policy import RETRY_AFTER_DEFAULT, TrackFailure
from classbeat.spotdown.song_match_policy import MAX_ALTERNATES, RETRY_PAUSE_SECONDS
from classbeat.spotdown.track_fit_policy import SlotNeed

POLICY_VERSION: Final = "song-analysis-1"
MAX_SOURCES: Final = 1 + MAX_ALTERNATES
MAX_REQUESTS: Final = 6
LENGTH_SLACK_S: Final = 10.0
STRUCTURE_OF_ROLE: Final[dict[str, frozenset[str]]] = {
    "warmup": frozenset({"low_energy_start"}),
}
_ALTERNATE_ON: Final = frozenset({"source_unavailable", "not_found", "processing_failed"})

Step = Literal["again", "alternate", "wait", "sections"]


@dataclass(frozen=True, slots=True)
class Tried:
    """One track's state over its requests."""

    source: int = 0  # the source asked for (0: the best)
    requests: int = 0  # requests made for this track, over all its sources
    retried_error: bool = False  # the one provider_error retry is spent (per track)
    retried_timeout: bool = False  # the one timeout retry of this source is spent


@dataclass(frozen=True, slots=True)
class Reaction:
    step: Step
    tried: Tried  # the state for the next request
    pause: float = 0.0  # seconds to wait before it (``again`` and ``wait``)
    reason: str | None = None  # ``sections``: the code why


def _sections(tried: Tried, reason: str) -> Reaction:
    return Reaction("sections", tried, reason=reason)


def _bounded(reaction: Reaction) -> Reaction:
    if reaction.step != "sections" and reaction.tried.requests >= MAX_REQUESTS:
        return _sections(reaction.tried, "too_many_requests")
    return reaction


def react(failure: TrackFailure, tried: Tried, sources: int) -> Reaction:
    """What a track does after ``failure`` (``tried.requests`` counts the request just made;
    ``sources``: how many sources the track has, at most ``MAX_SOURCES``)."""
    status = failure.status
    if status == "rate_limited":
        wait = failure.retry_after_s if failure.retry_after_s is not None else RETRY_AFTER_DEFAULT
        return _bounded(Reaction("wait", tried, max(wait, 0.0)))
    if status == "provider_error":
        if tried.retried_error:
            return _sections(tried, "provider_error")
        again = Tried(tried.source, tried.requests, True, tried.retried_timeout)
        return _bounded(Reaction("again", again, RETRY_PAUSE_SECONDS))
    timed_out = status == "processing_failed" and failure.failure_kind == "timeout"
    if timed_out and not tried.retried_timeout:
        again = Tried(tried.source, tried.requests, tried.retried_error, True)
        return _bounded(Reaction("again", again, RETRY_PAUSE_SECONDS))
    if status in _ALTERNATE_ON:
        if tried.source + 1 < min(sources, MAX_SOURCES):
            moved = Tried(tried.source + 1, tried.requests, tried.retried_error, False)
            return _bounded(Reaction("alternate", moved))
        return _sections(tried, status)
    if status == "alignment_failed":
        return _sections(tried, "alignment_failed")
    return _sections(tried, status)


def slot_need(role: str, bpm: float, length_sec: float) -> SlotNeed:
    """What the slot of a chosen song needs of its analysis."""
    return SlotNeed(
        bpm_min=bpm,
        bpm_max=bpm,
        min_s=max(float(length_sec) - LENGTH_SLACK_S, 0.0),
        max_s=float(length_sec) + LENGTH_SLACK_S,
        needs_exact=False,
        structure=STRUCTURE_OF_ROLE.get(role, frozenset()),
    )
