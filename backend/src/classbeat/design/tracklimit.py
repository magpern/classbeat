"""The daily limit of studio track downloads (ADR 0036 section 4.1, SR C4; the ``mixlimit``
pattern): at most ``DOWNLOADS_PER_DAY`` per user in any 24 hours, because each one downloads up to
60 MB from the gateway and analyses it. A cache hit costs none.

``swedebeat_track_downloads`` (migration 0053) is the record of every download started:
``check`` first refuses with ``track_limit`` (nothing written) when the window cannot hold all of a
run's misses, so a class's tracks are analysed together or not at all; then ``reserve`` adds **one
row right before each download** (in the fenced transaction that extends the lease for it), so a
failed download counts too, and a retried run (``upstream_unavailable``, a lost lease, a requeue
for a spotdown slot) reserves only the downloads it starts again, never the whole list once more
(SR C5, the C4 review's carry-over). Both run under a per-user advisory lock, so two runs of one
user never both take the last slots. The rows are not the maps: a forgotten analysis, a reconnect
or a Disconnect never gives the user a fresh day.
"""

from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.jobs import FencedSession, JobFailure

DOWNLOADS_PER_DAY: Final = 120  # three classes of 40 tracks
CATEGORY: Final = "track_limit"
KEEP_HOURS: Final = 48  # a user's older rows go with their next insert

_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
_USED = text(
    "SELECT count(*) FROM swedebeat_track_downloads "
    "WHERE owner_id = :owner_id AND created_at > now() - interval '24 hours'"
)
_ADD = text(
    "INSERT INTO swedebeat_track_downloads (owner_id) "
    "SELECT :owner_id FROM generate_series(1, :count)"
)
_TRIM = text(
    "DELETE FROM swedebeat_track_downloads "
    "WHERE owner_id = :owner_id AND created_at <= now() - make_interval(hours => :hours)"
)


def _key(owner_id: UUID) -> str:
    return f"swedebeat_track_downloads:{owner_id}"


async def used(db: AsyncSession, owner_id: UUID) -> int:
    """The user's track downloads in the last 24 hours."""
    return int((await db.execute(_USED, {"owner_id": owner_id})).scalar_one())


async def check(fs: FencedSession, count: int) -> None:
    """``track_limit`` unless ``count`` more downloads fit the user's window; writes nothing."""
    owner = fs.lease.owner_id
    await fs.db.execute(_LOCK, {"key": _key(owner)})
    if await used(fs.db, owner) + count > DOWNLOADS_PER_DAY:
        raise JobFailure(CATEGORY)


async def reserve(fs: FencedSession, count: int) -> None:
    """Take ``count`` of the user's slots in the job's fenced transaction, or fail the job
    ``track_limit`` (nothing is written then)."""
    if count <= 0:
        raise ValueError("reserve at least one download")
    owner = fs.lease.owner_id
    await fs.db.execute(_LOCK, {"key": _key(owner)})
    if await used(fs.db, owner) + count > DOWNLOADS_PER_DAY:
        raise JobFailure(CATEGORY)
    await fs.db.execute(_ADD, {"owner_id": owner, "count": count})
    await fs.db.execute(_TRIM, {"owner_id": owner, "hours": KEEP_HOURS})
