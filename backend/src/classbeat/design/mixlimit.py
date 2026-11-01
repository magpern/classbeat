"""The daily limit of new Swedebeat mix analyses (ADR 0016 section 7; A4): at most
``ANALYSES_PER_DAY`` per user in any 24 hours, because one analysis attaches a mix into the user's
MOWL account and fetches up to 250 MB. A cache hit costs none.

``swedebeat_analyses`` (migration 0020) is the record: ``reserve`` adds a row, in the worker,
**before** the listing, the attach and the fetch of a cache miss, so a failed fetch counts too. It
runs under a per-user advisory lock, so two runs of one user never both take the last slot, and
refuses with ``analysis_limit`` once the window holds the limit. The rows are not the maps: a
forgotten analysis, a reconnect or a Disconnect never gives the user a fresh day.

``used`` is what a job's ``admit`` reads to refuse early (``classbeat.design.mixadmit``); the
worker's ``reserve`` is the enforcement.
"""

from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.jobs import FencedSession, JobFailure

ANALYSES_PER_DAY: Final = 10
CATEGORY: Final = "analysis_limit"
KEEP_HOURS: Final = 48  # a user's older rows go with their next insert

_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_USED = text(
    "SELECT count(*) FROM swedebeat_analyses "
    "WHERE owner_id = :owner_id AND created_at > now() - interval '24 hours'"
)
_ADD = text("INSERT INTO swedebeat_analyses (owner_id) VALUES (:owner_id)")
_TRIM = text(
    "DELETE FROM swedebeat_analyses "
    "WHERE owner_id = :owner_id AND created_at <= now() - make_interval(hours => :hours)"
)


def _key(owner_id: UUID) -> str:
    return f"swedebeat_analyses:{owner_id}"


async def used(db: AsyncSession, owner_id: UUID) -> int:
    """The user's new analyses in the last 24 hours."""
    return int((await db.execute(_USED, {"owner_id": owner_id})).scalar_one())


async def reserve(fs: FencedSession) -> None:
    """Take one of the user's slots in the job's fenced transaction, or fail the job
    ``analysis_limit`` (nothing is written then)."""
    owner = fs.lease.owner_id
    await fs.db.execute(_LOCK, {"key": _key(owner)})
    if await used(fs.db, owner) >= ANALYSES_PER_DAY:
        raise JobFailure(CATEGORY)
    await fs.db.execute(_ADD, {"owner_id": owner})
    await fs.db.execute(_TRIM, {"owner_id": owner, "hours": KEEP_HOURS})
