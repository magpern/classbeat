"""The plain sentence for a job's failure category, for the page (ADR 0008 section 9, ADR 0009
section 8, ADR 0016 section 5). Song picking, the Spotify connection and a waiting Swedebeat
catalogue match have a sentence here; the other categories keep the page's own wording
(``None``). Never a provider's text, a token or a catalogue body.

``rate_limited`` and ``upstream_unavailable`` are MOWL's and the model's categories too: they get
Spotify's sentence only when the job's last failure came from Spotify (``jobs.spotify_failure``),
also while the job waits for its retry. ``rate_limited`` and ``quota_exhausted`` name the time
from Spotify's last ``Retry-After`` (``jobs.spotify_retry_after``). A ``swedebeat_match`` whose
stored failure is Swedebeat's own 429 names that wait while the job is still queued, and never
a longer wait than the pause cap. Once that match has failed, the sentence does not promise
another try. It does not count as a Spotify rate limit."""

import math
from datetime import datetime
from typing import Final

from classbeat.jobs.errors import RATE_LIMIT_CAP_SECONDS
from classbeat.sentence import Sentence
from classbeat.spotify.errors import SENTENCES as SPOTIFY_SENTENCES
from classbeat.spotify.errors import sentence_of as spotify_sentence_of

SONG_SENTENCES: Final[dict[str, str]] = {
    "consent_missing": "Allow the working playlist in your Spotify settings first.",
    "pool_unusable": (
        "Your working playlist was changed: make it private again or set it up again."
    ),
    "pool_missing": "Your working playlist was gone from Spotify; trying again by itself.",
    "pool_not_synced": ("MOWL has not caught up with the working playlist; try again in a minute."),
    "playlist_mismatch": (
        "Spotify's playlist did not match the approved songs; nothing was designed. "
        "Create it again."
    ),
    "pool_too_small": "Not enough usable songs were found; try a wider style or era.",
    "account_changed": (
        "Your Spotify account is not the one these songs were chosen for. Ask for new songs."
    ),
    "pool_busy": "Another song pick is still running.",
    # ADR 0016 sections 3 and 4: a Swedebeat mix. Never the host, a link or a file's own words.
    "media_host_not_allowed": (
        "The mix is in your MOWL account, but this server cannot analyse new mixes yet. "
        "Try one analysed before."
    ),
    "media_refused": "The audio of this mix could not be fetched. Nothing was designed.",
    "analysis_failed": "The audio of this mix could not be analysed. Try another mix.",
    "feature_off": "This feature is switched off on this server right now. Nothing was designed.",
    "analysis_limit": (
        "You have analysed 10 new Swedebeat mixes in the last 24 hours. Try again tomorrow, "
        "or use one analysed before."
    ),
    "handoff_expired": (
        "That check expired. Start again from the Swedebeat step; nothing was fetched."
    ),
}
# Spotify categories that are not also categories of MOWL or of our own (``rate_limited`` and
# ``upstream_unavailable`` keep the generic wording).
_SPOTIFY_ONLY: Final = frozenset(SPOTIFY_SENTENCES) - {
    "rate_limited",
    "upstream_unavailable",
    "account_changed",
}


# The job kind whose 429 sentence is Swedebeat's, not Spotify's (``swedebeat_match.revision.KIND``).
_SWEDEBEAT_MATCH: Final = "swedebeat_match"
_SWEDEBEAT_WAIT: Final = "Swedebeat asks to slow down; trying again in about {minutes}."
# The attempts are used up. This must not say the job will try again.
_SWEDEBEAT_STOPPED: Final = "Swedebeat asks to slow down. This match has stopped."


def _minutes(retry_after: int | None) -> int:
    """Whole minutes of a wait, at least one, the same rounding as Spotify's sentence.

    A hint longer than ``RATE_LIMIT_CAP_SECONDS`` is named as that cap, which is also the
    longest pause."""
    raw = retry_after if retry_after is not None and retry_after > 0 else 60
    seconds = min(raw, int(RATE_LIMIT_CAP_SECONDS))
    return max(1, -(-seconds // 60))


def _swedebeat_wait(retry_after: int | None) -> Sentence:
    """The queued catalogue 429 sentence. The words are ours: no token, body or provider error."""
    minutes = _minutes(retry_after)
    unit = f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return Sentence("swedebeat.wait", _SWEDEBEAT_WAIT.format(minutes=unit), {"count": minutes})


def _swedebeat_rate_limit(state: str, category: str | None, spotify_failure: str | None) -> bool:
    """A match whose stored failure is Swedebeat's 429: waiting (category NULL) or failed on it."""
    if spotify_failure != "rate_limited":
        return False
    if state == "queued" and category is None:
        return True
    return state == "failed" and category == "rate_limited"


def job_sentence_of(
    state: str,
    category: str | None,
    spotify_failure: str | None,
    retry_after: int | None,
    available_at: datetime,
    now: datetime,
    kind: str | None = None,
) -> Sentence | None:
    """The sentence the page shows for a job: its failure's, or, while a retry after a Spotify
    rate limit or outage is pending, that one's (``category`` is NULL then). A Swedebeat match
    waiting on its own 429 names that wait. A failed match says it has stopped."""
    if kind == _SWEDEBEAT_MATCH and _swedebeat_rate_limit(state, category, spotify_failure):
        if state == "failed":
            return Sentence("swedebeat.stopped", _SWEDEBEAT_STOPPED)
        hint = retry_after
        if hint is None:
            hint = max(1, math.ceil((available_at - now).total_seconds()))
        return _swedebeat_wait(hint)
    if state == "failed" and category is not None and category == spotify_failure:
        return spotify_sentence_of(category, retry_after)
    if state == "queued" and category is None and spotify_failure in _RETRIED:
        hint = retry_after
        if spotify_failure == "rate_limited" and hint is None:
            hint = max(1, math.ceil((available_at - now).total_seconds()))
        return spotify_sentence_of(spotify_failure, hint)
    if state != "failed":
        return None
    return failure_sentence_of(category)


def job_sentence(
    state: str,
    category: str | None,
    spotify_failure: str | None,
    retry_after: int | None,
    available_at: datetime,
    now: datetime,
    kind: str | None = None,
) -> str | None:
    """The English text of ``job_sentence_of``."""
    found = job_sentence_of(state, category, spotify_failure, retry_after, available_at, now, kind)
    return None if found is None else found.text


_RETRIED: Final = frozenset({"rate_limited", "upstream_unavailable"})


def failure_sentence_of(category: str | None) -> Sentence | None:
    if category is None:
        return None
    if category in SONG_SENTENCES:
        return Sentence(f"job.{category}", SONG_SENTENCES[category])
    if category in _SPOTIFY_ONLY:
        return spotify_sentence_of(category)
    return None


def failure_sentence(category: str | None) -> str | None:
    found = failure_sentence_of(category)
    return None if found is None else found.text
