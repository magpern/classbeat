"""How long a fresh mix analysis took (ADR 0026 stage 1, #735).

``note_analysis_times(fs, fetch_seconds, decode_seconds)`` runs in the fenced transaction that
stores a fresh Swedebeat mix analysis (``design.swedebeat_music``, next to ``note_fresh_analysis``):
it sets ``jobs.analysis_fetch_ms``, ``jobs.analysis_decode_ms`` and ``jobs.analysed_at`` (migration
``0039_analysis_metrics``). A stale attempt cannot set them (the update is fenced like every other
write), a cached mix or a failed analysis sets nothing, and a later fresh analysis of the same job
overwrites them. ``queue_stats()`` reads them for the p95 of the last seven days; the queue wait
comes from the ``claimed`` events (``queue_claim``), not from here. Whole milliseconds only.
"""

from typing import Final

from sqlalchemy import text

from classbeat.jobs.errors import LeaseLost
from classbeat.jobs.fence import FencedSession

# A day: far beyond any fetch (300 s) or analysis (180 s) deadline, and within ``integer``.
MAX_MS: Final = 86_400_000

_SET = text(
    "UPDATE jobs SET analysis_fetch_ms = :fetch_ms, analysis_decode_ms = :decode_ms, "
    "analysed_at = now() "
    "WHERE id = :job_id AND owner_id = :owner_id AND lease_version = :v AND state = 'running'"
)


def to_ms(seconds: float) -> int:
    """``seconds`` as whole milliseconds, clamped to ``0..MAX_MS`` (a clock that went backwards
    or a fake clock gives 0, never a negative)."""
    return min(max(round(seconds * 1000), 0), MAX_MS)


async def note_analysis_times(
    fs: FencedSession, fetch_seconds: float, decode_seconds: float
) -> None:
    """Record the fetch and the decode of the job's fresh analysis (inside the storing
    transaction)."""
    params = {
        **fs.lease.params(),
        "fetch_ms": to_ms(fetch_seconds),
        "decode_ms": to_ms(decode_seconds),
    }
    result = await fs.db.execute(_SET, params)
    if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
        raise LeaseLost
