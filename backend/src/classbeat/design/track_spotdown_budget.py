"""A design job's spotdown budget for its studio tracks and mixes (``spotdown_studio_budgets``,
ADR 0040 section 8; migration 0064): the bounds of section 8 hold **per attempt**, over the runs
of that attempt (a shutdown hand-back or a spotdown-slot requeue keeps them), not per run. A run
that ends in a retryable category deletes the row (``jobs.transitions.retry``, ADR 0040 amendment
"spotdown is the only analysis path", item 4), so the next attempt starts with a full budget and no
outage flag; a lease-lost requeue keeps them.

- ``open``: in the job's fenced transaction, the job's row (made on its first spotdown use, so
  ``started_at`` is when the budget began), the database's ``now()`` and whether spotdown was
  found unavailable by an earlier run of the attempt;
- ``mark_outage``: spotdown is unavailable for this attempt: a later run of it ends
  ``analysis_unavailable`` without calling spotdown.

Filtered on ``owner_id`` itself as well as through row security. Nothing here logs.
"""

from datetime import datetime

from sqlalchemy import text

from classbeat.jobs import JobRun

_OPEN = text(
    "INSERT INTO spotdown_studio_budgets (owner_id, job_id) VALUES (:owner_id, :job_id) "
    "ON CONFLICT (owner_id, job_id) DO NOTHING"
)
_READ = text(
    "SELECT now() AS now, started_at, outage FROM spotdown_studio_budgets "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)
_OUTAGE = text(
    "UPDATE spotdown_studio_budgets SET outage = true "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)


async def open(run: JobRun) -> tuple[datetime, datetime, bool]:
    params = {"owner_id": run.lease.owner_id, "job_id": run.lease.job_id}
    async with run.fenced() as fs:
        await fs.db.execute(_OPEN, params)
        row = (await fs.db.execute(_READ, params)).one()
    return row.now, row.started_at, bool(row.outage)


async def mark_outage(run: JobRun) -> None:
    params = {"owner_id": run.lease.owner_id, "job_id": run.lease.job_id}
    async with run.fenced() as fs:
        await fs.db.execute(_OUTAGE, params)
