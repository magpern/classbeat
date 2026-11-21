"""Reset of a job's spotdown wait budget and outage flag (ADR 0040 amendment "spotdown is the only
analysis path", item 4; #1352, #1354).

The song stages keep the seconds a job has waited for spotdown and whether an outage was found in
``spotdown_song_budgets`` (``classbeat.songs.pick_match_budget``). A budget belongs to **one
attempt**: it is reset only when

- a run ends in a retryable category (``transitions.retry``, a category in ``RETRYABLE``; not
  ``operation_busy`` or ``pool_not_synced``),
  because the next attempt is a fresh try (it will find spotdown up, or end ``analysis_unavailable``
  again with a fresh budget); and
- Approve starts a phase (``approval.approve``).

A lease-lost requeue, ``hand_back``, ``operation_busy`` and a wait for the analysis slot keep what
was spent. One statement, filtered on ``owner_id`` and ``job_id`` as well as through row security.
A job with no row yet is untouched (nothing to reset).
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RESET = text(
    "UPDATE spotdown_song_budgets SET spent_seconds = 0, fallback = false "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)


async def reset(db: AsyncSession, params: dict[str, Any]) -> None:
    """Reset the budget of the job named by ``params`` (``owner_id`` and ``job_id`` are read)."""
    await db.execute(_RESET, {"owner_id": params["owner_id"], "job_id": params["job_id"]})
