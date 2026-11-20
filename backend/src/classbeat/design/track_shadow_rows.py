"""The rows of shadow mode (``spotdown_shadow_tracks``, ADR 0040 section 9; migration 0063): each
written in the job's own fenced transaction, filtered on ``owner_id`` itself as well as through
row security, and on the job.

- ``open_row``: a ``pending`` row with the local side's metrics, before anything is sent to
  spotdown; the lease is extended over the relay in the same transaction, and the owner's rows
  past ``KEPT_DAYS`` go.
- ``first_started``: the database's ``now()`` and the job's first relay (its wait budget).
- ``close_rows``: each still ``pending`` row of the job gets its outcome, detail, metrics and
  provenance, in one transaction.

Nothing here logs.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import text

from classbeat.jobs import JobRun
from classbeat.jsonb_text_calc import jsonb_text_size
from classbeat.spotdown.shadow_record_model import ShadowRecord

KEPT_DAYS: Final = 90
METRICS_MAX: Final = 16384  # the table's checks
PROVENANCE_MAX: Final = 65536

_OPEN = text(
    "INSERT INTO spotdown_shadow_tracks (owner_id, job_id, outcome, metrics) "
    "VALUES (:owner_id, :job_id, 'pending', CAST(:metrics AS jsonb)) RETURNING id"
)
_EXPIRE = text(
    "DELETE FROM spotdown_shadow_tracks "
    "WHERE owner_id = :owner_id AND started_at <= now() - make_interval(days => :days)"
)
_FIRST = text(
    "SELECT now() AS now, (SELECT min(started_at) FROM spotdown_shadow_tracks "
    "WHERE owner_id = :owner_id AND job_id = :job_id) AS first"
)
_CLOSE = text(
    "UPDATE spotdown_shadow_tracks SET outcome = :outcome, detail = :detail, "
    "metrics = CAST(:metrics AS jsonb), provenance = CAST(:provenance AS jsonb), "
    "finished_at = now() "
    "WHERE owner_id = :owner_id AND job_id = :job_id AND id = :id AND outcome = 'pending'"
)


def _dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


async def open_row(run: JobRun, metrics: Mapping[str, object], lease_seconds: float) -> UUID:
    owner, job = run.lease.owner_id, run.lease.job_id
    async with run.fenced() as fs:
        await fs.extend_lease(lease_seconds)
        await fs.db.execute(_EXPIRE, {"owner_id": owner, "days": KEPT_DAYS})
        params = {"owner_id": owner, "job_id": job, "metrics": _dump(dict(metrics))}
        row_id = (await fs.db.execute(_OPEN, params)).scalar_one()
    return row_id


async def first_started(run: JobRun) -> tuple[datetime, datetime | None]:
    params = {"owner_id": run.lease.owner_id, "job_id": run.lease.job_id}
    async with run.fenced() as fs:
        row = (await fs.db.execute(_FIRST, params)).one()
    return row.now, row.first


def _params(record: ShadowRecord) -> dict[str, object]:
    """The record as bind values; one the table would refuse is stored as ``internal``."""
    try:
        metrics = dict(record.metrics or {})
        provenance = None if record.provenance is None else dict(record.provenance)
        dumped = _dump(metrics), None if provenance is None else _dump(provenance)
        fits = jsonb_text_size(metrics) <= METRICS_MAX and (
            provenance is None or jsonb_text_size(provenance) <= PROVENANCE_MAX
        )
    except (TypeError, ValueError):
        fits, dumped = False, ("{}", None)
    if not fits:
        return {"outcome": "internal", "detail": "too_large", "metrics": "{}", "provenance": None}
    return {
        "outcome": record.outcome,
        "detail": record.detail,
        "metrics": dumped[0],
        "provenance": dumped[1],
    }


async def close_rows(run: JobRun, records: Mapping[UUID, ShadowRecord]) -> None:
    if not records:
        return
    base = {"owner_id": run.lease.owner_id, "job_id": run.lease.job_id}
    async with run.fenced() as fs:
        for row_id, record in records.items():
            await fs.db.execute(_CLOSE, {**base, "id": row_id, **_params(record)})
