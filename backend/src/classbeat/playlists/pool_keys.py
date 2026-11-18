"""The key of a pool write (ADR 0008 section 2; #1188 review B1).

A pool write's key is ``<job_id>:pool:<h16>`` (``h16``: the first 16 hex characters of the list's
hash). The protocol reuses a ``succeeded`` row of the same key with no call, which is right only
while the playlist still holds that list. A job that writes the working copy more than once (a
composer's fill and swap: the pool, then the chosen songs in play order, ADR 0038 section 5)
breaks that on a retry: after its second write, a retried attempt's first write of an earlier
list would be "reused" while the playlist holds the later one, and the analysis waits for ids
that are not there (``pool_not_synced``).

So ``write_key`` picks the key from this job's own rows of that list (``<h16>`` and its
rewrites ``<h16>:<n>``, ``n`` from 1), newest first:

- none: the base key, a first write;
- the newest is not ``succeeded`` (``pending``, ``uncertain``, ``failed``): that key, so the
  protocol reconciles or retries it exactly as before (at most once, reconcile before retry);
- the newest ``succeeded``, the pool's ``last_hash`` (set by every succeeded pool write, under
  the working copy's lock) is this list, and it is this job's newest pool write of **any** list:
  that key, reused with no call (the playlist holds it). A later write of another list that did
  not end ``succeeded`` (a crash in the middle of it: ``pending`` or ``uncertain``) may have
  reached the playlist while ``last_hash`` still names this list, so that is never trusted;
- the newest ``succeeded`` but the pool holds another list since: the next rewrite key
  ``<h16>:<n + 1>``, a new write of the same list (an absolute replace, safe to repeat).

Called inside ``write_pool``'s working-copy lock, so no other write moves ``last_hash`` meanwhile.
"""

from typing import Final
from uuid import UUID

from sqlalchemy import text

from classbeat.jobs import JobRun
from classbeat.operations.kinds import SPOTIFY_REPLACE_KIND
from classbeat.playlists import common, replace

MAX_REWRITES: Final = 9999  # the key pattern's bound (``operations.kinds``)

_ROWS = text(
    "SELECT idempotency_key, state FROM external_operations WHERE owner_id = :owner_id "
    "AND kind = :kind AND (idempotency_key = :key OR idempotency_key LIKE :like)"
)
# This job's newest pool write of any list (its key), by creation.
_NEWEST_OF_JOB = text(
    "SELECT idempotency_key FROM external_operations WHERE owner_id = :owner_id "
    "AND kind = :kind AND idempotency_key LIKE :job_like ORDER BY created_at DESC, id DESC LIMIT 1"
)
_LAST_HASH = text("SELECT last_hash FROM spotify_pool WHERE owner_id = :owner_id AND id = :pool_id")


def _rewrite(key: str, base: str) -> int:
    return 0 if key == base else int(key.rsplit(":", 1)[1])


async def write_key(run: JobRun, pool_id: UUID, track_ids: tuple[str, ...]) -> str:
    """The key of this write of ``track_ids`` (module docstring)."""
    digest = common.ids_hash(track_ids)
    base = replace.pool_key(run.lease.job_id, digest)
    owner = run.lease.owner_id
    async with run.fenced() as fs:
        params = {"owner_id": owner, "kind": SPOTIFY_REPLACE_KIND, "key": base, "like": f"{base}:%"}
        rows = (await fs.db.execute(_ROWS, params)).all()
        job_like = {
            "owner_id": owner,
            "kind": SPOTIFY_REPLACE_KIND,
            "job_like": f"{run.lease.job_id}:pool:%",
        }
        newest_of_job = (await fs.db.execute(_NEWEST_OF_JOB, job_like)).scalar_one_or_none()
        held = (
            await fs.db.execute(_LAST_HASH, {"owner_id": owner, "pool_id": pool_id})
        ).scalar_one_or_none()
    if not rows:
        return base
    newest = max(rows, key=lambda r: _rewrite(r.idempotency_key, base))
    if newest.state != "succeeded":
        return newest.idempotency_key  # the protocol reconciles or retries it, as ever
    holds = held is not None and bytes(held) == digest
    if holds and newest_of_job == newest.idempotency_key:
        return newest.idempotency_key  # the playlist holds it: reused, no call
    following = _rewrite(newest.idempotency_key, base) + 1
    return f"{base}:{min(following, MAX_REWRITES)}"
