"""The plain sentence for a job's failure category, for the page (ADR 0008 section 9, ADR 0009
section 8). Only the categories song picking and the Spotify connection added have one here; the
others keep the page's own wording (``None``). Never a provider's text.

``rate_limited`` and ``upstream_unavailable`` are MOWL's and the model's categories too: they get
Spotify's sentence only when the job's last failure came from Spotify (``jobs.spotify_failure``),
also while the job waits for its retry. ``rate_limited`` and ``quota_exhausted`` name the time
from Spotify's last ``Retry-After`` (``jobs.spotify_retry_after``)."""

import math
from datetime import datetime
from typing import Final

from classbeat.spotify.errors import SENTENCES as SPOTIFY_SENTENCES
from classbeat.spotify.errors import sentence as spotify_sentence

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
}
# Spotify categories that are not also categories of MOWL or of our own (``rate_limited`` and
# ``upstream_unavailable`` keep the generic wording).
_SPOTIFY_ONLY: Final = frozenset(SPOTIFY_SENTENCES) - {
    "rate_limited",
    "upstream_unavailable",
    "account_changed",
}


def job_sentence(
    state: str,
    category: str | None,
    spotify_failure: str | None,
    retry_after: int | None,
    available_at: datetime,
    now: datetime,
) -> str | None:
    """The sentence the page shows for a job: its failure's, or, while a retry after a Spotify
    rate limit or outage is pending, that one's (``category`` is NULL then)."""
    if state == "failed" and category is not None and category == spotify_failure:
        return spotify_sentence(category, retry_after)
    if state == "queued" and category is None and spotify_failure in _RETRIED:
        hint = retry_after
        if spotify_failure == "rate_limited" and hint is None:
            hint = max(1, math.ceil((available_at - now).total_seconds()))
        return spotify_sentence(spotify_failure, hint)
    if state != "failed":
        return None
    return failure_sentence(category)


_RETRIED: Final = frozenset({"rate_limited", "upstream_unavailable"})


def failure_sentence(category: str | None) -> str | None:
    if category is None:
        return None
    if category in SONG_SENTENCES:
        return SONG_SENTENCES[category]
    if category in _SPOTIFY_ONLY:
        return spotify_sentence(category)
    return None
