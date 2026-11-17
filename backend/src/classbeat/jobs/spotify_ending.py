"""The Spotify bookkeeping of a job's attempt (ADR 0009 section 8), fenced like every worker write.

Split out of ``classbeat.jobs.transitions`` (which re-exports it): the Spotify requests the job sent
(``save_spotify_calls``) and how an attempt ended for Spotify (``note_spotify_ending``).
"""

from sqlalchemy import text

from classbeat.jobs.errors import LeaseLost
from classbeat.jobs.fence import FencedSession

_SPOTIFY_CALLS = text(
    "UPDATE jobs SET spotify_calls = :calls "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'"
)
# ADR 0009 section 8: the attempts in a row that ended with a Spotify 429 (any other ending
# resets them), the category of a Spotify failure whose sentence is Spotify's, and the last hint,
# kept only while the attempts end with a Spotify 429 (``rate_limited`` or ``quota_exhausted``;
# any other ending clears it).
_SPOTIFY_ENDING = text(
    "UPDATE jobs SET spotify_rate_limits = CASE WHEN :rate_limited "
    "THEN LEAST(spotify_rate_limits + 1, :cap) ELSE 0 END, "
    "spotify_failure = :failure, "
    "spotify_retry_after = CASE WHEN :failure IN ('rate_limited', 'quota_exhausted') "
    "THEN coalesce(:retry_after, spotify_retry_after) END "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running' "
    "RETURNING spotify_rate_limits"
)
# The Spotify categories whose sentence on the job page is Spotify's (``spotify_failure``).
SPOTIFY_NOTED = frozenset({"rate_limited", "upstream_unavailable", "quota_exhausted"})


async def save_spotify_calls(fs: FencedSession, calls: int) -> None:
    """The Spotify requests the job has sent so far (``CLASSBEAT_SPOTIFY_CALLS_PER_JOB``)."""
    result = await fs.db.execute(_SPOTIFY_CALLS, {**fs.lease.params(), "calls": calls})
    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
        raise LeaseLost


async def note_spotify_ending(
    fs: FencedSession,
    *,
    rate_limited: bool,
    failure: str | None,
    retry_after: int | None,
    cap: int,
) -> int:
    """Record how the attempt ended for Spotify: ``rate_limited`` (a Spotify 429) counts one
    more in a row, anything else resets the count; ``failure`` is the Spotify category whose
    sentence the page shows (``SPOTIFY_NOTED``) or None; ``retry_after`` the hint (kept when
    None). Returns the attempts in a row that ended with a Spotify 429."""
    if failure is not None and failure not in SPOTIFY_NOTED:
        raise ValueError("not a category with a Spotify sentence")
    row = (
        await fs.db.execute(
            _SPOTIFY_ENDING,
            {
                **fs.lease.params(),
                "rate_limited": rate_limited,
                "failure": failure,
                "retry_after": retry_after,
                "cap": cap,
            },
        )
    ).first()
    if row is None:
        raise LeaseLost
    return int(row.spotify_rate_limits)
