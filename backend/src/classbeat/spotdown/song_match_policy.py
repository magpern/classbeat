"""The match stage of a Spotify song pick (ADR 0040 sections 2 and 8, plan sections 1 and 6;
SD-P6 stage A, #1303): what each ``POST /v2/match`` outcome means for a candidate, the bounded
reactions and the per-class bounds. Pure: no I/O.

**What a candidate is used for** (``TrackMatch.use``), after its reactions:

- ``timing``: ``matched`` (a ``confident`` best candidate). The later analysis stage may analyse
  its source (best, then at most ``MAX_ALTERNATES`` alternates); until then, and whenever that
  fails, the candidate keeps today's MOWL pool sections.
- ``sections``: kept with MOWL's sections only, **never used for timing**: ``uncertain``, a
  ``provider_error`` still there after its one retry, a ``rate_limited`` the budget could not
  wait for.
- ``missing``: ``not_found`` or ``source_unavailable`` (the match carries no alternate for it):
  genuinely missing, so a spare, then a replacement round (``song_swap_calc``).

**Reactions** (``reaction``): ``rate_limited`` waits its (clamped) ``retry_after_s`` and asks
again, not counted as an attempt; ``provider_error`` is retried once after ``RETRY_PAUSE_SECONDS``;
every other outcome is final. **Provider outage** (``provider_outage``): at least
``OUTAGE_MIN_TRACKS`` tracks and at least half of a batch still ``provider_error`` after the
retry: no swaps, the whole class falls back to today's path (MOWL sections).

**The budget** (``remaining``, ``wait_for``): a song job waits for spotdown (retry pauses and
``rate_limited`` waits) at most ``WAIT_SECONDS`` (the "about 10 min" of section 8) **in all, over
every run of the job** (``classbeat.songs.pick_match_budget``); a wait that does not fit is not
made. At most ``MAX_ROUNDS`` replacement rounds and ``MAX_PASSES`` requests per batch.
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

POLICY_VERSION: Final = "song-match-1"
WAIT_SECONDS: Final = 600.0
RETRY_PAUSE_SECONDS: Final = 5.0
OUTAGE_MIN_TRACKS: Final = 3
MAX_ALTERNATES: Final = 2
MAX_ROUNDS: Final = 3
MAX_PASSES: Final = 6  # one request, its retry, and rate_limited waits (each inside the budget)

Use = Literal["timing", "sections", "missing"]
Reaction = Literal["final", "retry", "wait"]
USES: Final[frozenset[str]] = frozenset({"timing", "sections", "missing"})


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
    if isinstance(outcome, UncertainMatch):
        return TrackMatch(
            "sections",
            "uncertain",
            outcome.best.band,
            outcome.best.recording_match_confidence,
            outcome.matcher_version,
        )
    if outcome.status in ("not_found", "source_unavailable"):
        return TrackMatch("missing", outcome.status)
    # provider_error after its retry, rate_limited past the budget, or a job-only status that
    # ``/v2/match`` never sends: keep the song with MOWL's sections.
    return TrackMatch("sections", outcome.status)


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
