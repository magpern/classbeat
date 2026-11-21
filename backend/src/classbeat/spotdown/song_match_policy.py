"""The match stage of a Spotify song pick (ADR 0040 amendment "spotdown is the only analysis
path", items 4 and 5; SD-P6 stage A, #1303, #1354): what each ``POST /v2/match`` outcome means for
a candidate, the bounded reactions and the per-class bounds. Pure: no I/O.

**What a candidate is used for** (``TrackMatch.use``), after its reactions:

- ``timing``: ``matched`` (a ``confident`` best candidate). The analysis stage analyses its source
  (best, then at most ``MAX_ALTERNATES`` alternates).
- ``missing``: everything else a single track can end with: ``uncertain``, ``not_found``,
  ``source_unavailable`` and a ``provider_error`` still there after its one retry. A missing song
  gets a spare, then a replacement round (``song_swap_calc``), then ``analysis_failed``. A song is
  never kept on MOWL's sections.

**Reactions** (``reaction``): ``rate_limited`` waits its (clamped) ``retry_after_s`` and asks
again, not counted as an attempt; ``provider_error`` is retried once after ``RETRY_PAUSE_SECONDS``;
every other outcome is final. **Provider outage** (``provider_outage``): at least
``OUTAGE_MIN_TRACKS`` tracks and at least half of a batch still ``provider_error`` after the
retry: the job ends ``analysis_unavailable`` (retryable), no swaps.

**The budget** (``remaining``, ``wait_for``): a song job waits for spotdown (retry pauses and
``rate_limited`` waits) at most ``WAIT_SECONDS`` (the "about 10 min" of section 8) **in all, over
every run of one attempt** (``classbeat.songs.pick_match_budget``); a wait that does not fit is not
made and ends the job ``analysis_unavailable``. At most ``MAX_ROUNDS`` replacement rounds and
``MAX_PASSES`` requests per batch.
"""

from dataclasses import dataclass, field
from typing import Final, Literal

from classbeat.spotdown.failure_policy import (
    Matched,
    MatchOutcome,
    TrackFailure,
    UncertainMatch,
)
from classbeat.spotdown.match_model import Candidate

POLICY_VERSION: Final = "song-match-2"
WAIT_SECONDS: Final = 600.0
RETRY_PAUSE_SECONDS: Final = 5.0
OUTAGE_MIN_TRACKS: Final = 3
MAX_ALTERNATES: Final = 2
MAX_ROUNDS: Final = 3
MAX_PASSES: Final = 6  # one request, its retry, and rate_limited waits (each inside the budget)

Use = Literal["timing", "missing"]
Reaction = Literal["final", "retry", "wait"]
USES: Final[frozenset[str]] = frozenset({"timing", "missing"})


@dataclass(frozen=True, slots=True)
class TrackMatch:
    """One candidate's match, as the song stage keeps it (in memory, for this run)."""

    use: Use
    status: str  # the final match status (a ``match-v1`` status)
    band: str | None = None
    confidence: float | None = None
    matcher_version: str | None = None
    # The sources the analysis stage may try, best first (at most 1 + ``MAX_ALTERNATES``), and a
    # cached analysis of the best one. Never logged, never stored with the class.
    sources: tuple[Candidate, ...] = field(default=(), repr=False)


def track_match(outcome: MatchOutcome) -> TrackMatch:
    """A final outcome (after ``reaction``) as the candidate's match."""
    if isinstance(outcome, Matched):
        return TrackMatch(
            "timing",
            "matched",
            outcome.best.band,
            outcome.best.recording_match_confidence,
            outcome.matcher_version,
            (outcome.best, *outcome.alternates[:MAX_ALTERNATES]),
        )
    # uncertain, not_found, source_unavailable, provider_error after its retry, or a job-only
    # status that ``/v2/match`` never sends: the song is missing. (``rate_limited`` past the budget
    # never gets here: the caller ends the job ``analysis_unavailable``.)
    if isinstance(outcome, UncertainMatch):
        return TrackMatch(
            "missing",
            "uncertain",
            outcome.best.band,
            outcome.best.recording_match_confidence,
            outcome.matcher_version,
        )
    return TrackMatch("missing", outcome.status)


def reaction(outcome: MatchOutcome, retried: bool) -> Reaction:
    """What to do after ``outcome``; ``retried``: this track's one retry is spent."""
    if isinstance(outcome, TrackFailure):
        if outcome.status == "rate_limited":
            return "wait"
        if outcome.status == "provider_error" and not retried:
            return "retry"
    return "final"


def provider_outage(provider_errors: int, batch: int) -> bool:
    """Whether a batch's final ``provider_error`` tracks make a provider outage (section 8)."""
    return provider_errors >= OUTAGE_MIN_TRACKS and 2 * provider_errors >= batch


def wait_for(wanted: float, left: float) -> float | None:
    """The wait to make before asking again (at least 0); ``None`` when it does not fit in what
    is ``left`` of the job's budget."""
    wait = max(wanted, 0.0)
    return wait if wait < left else None


def remaining(spent: float) -> float:
    """Seconds left of the job's budget after ``spent`` seconds of waits."""
    return min(max(WAIT_SECONDS - spent, 0.0), WAIT_SECONDS)
