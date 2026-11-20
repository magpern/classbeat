"""A song job's spotdown match budget (``spotdown_song_budgets``, ADR 0040 section 8; migration
0066; SD-P6 stage A, #1303): the bounds of section 8 hold **per class**, over every run of the
job, not per run.

- ``open``: in the job's fenced transaction, the job's row (made on its first use of the match)
  with the seconds already waited and whether an earlier run fell back to today's path;
- ``charge``: add a wait to the job's total **before** it is made (a run stopped during the wait
  has spent it), capped at a day;
- ``mark_fallback``: spotdown was unreachable or a provider outage was found: every later run of
  the job uses today's path without calling spotdown.

Filtered on ``owner_id`` itself as well as through row security, and on the job. Nothing here
logs. The later stages of the Spotify flow (analysis, refinement) share this budget.
"""

from sqlalchemy import text

from classbeat.jobs import JobRun

_OPEN = text(
    "INSERT INTO spotdown_song_budgets (owner_id, job_id) VALUES (:owner_id, :job_id) "
    "ON CONFLICT (owner_id, job_id) DO NOTHING"
)
_READ = text(
    "SELECT spent_seconds, fallback FROM spotdown_song_budgets "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)
_CHARGE = text(
    "UPDATE spotdown_song_budgets SET spent_seconds = least(spent_seconds + :seconds, 86400) "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)
_FALLBACK = text(
    "UPDATE spotdown_song_budgets SET fallback = true "
    "WHERE owner_id = :owner_id AND job_id = :job_id"
)


def _params(run: JobRun) -> dict[str, object]:
    return {"owner_id": run.lease.owner_id, "job_id": run.lease.job_id}


async def open(run: JobRun) -> tuple[float, bool]:
    """(seconds waited so far, fallen back)."""
    async with run.fenced() as fs:
        await fs.db.execute(_OPEN, _params(run))
        row = (await fs.db.execute(_READ, _params(run))).one()
    return float(row.spent_seconds), bool(row.fallback)


async def charge(run: JobRun, seconds: float) -> None:
    if not seconds > 0:
        return
    async with run.fenced() as fs:
        await fs.db.execute(_CHARGE, {**_params(run), "seconds": float(seconds)})


async def mark_fallback(run: JobRun) -> None:
    async with run.fenced() as fs:
        await fs.db.execute(_FALLBACK, _params(run))
