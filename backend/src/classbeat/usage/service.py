"""Reads of ``agent_runs`` for the usage views (ADR 0007 section 10).

Every statement filters on ``owner_id = ctx.user_id`` and RLS answers for the session's user.
Figures are estimates at the price table the run was priced with (``price_version``); no row is
re-priced. A run that never finished (a crash) counts with whatever it recorded.

A deleted class takes its job with it (``agent_runs.job_id`` becomes NULL), so a row without a
job counts as a job of its own: the ``jobs`` figure can then be higher than the real count when
a deleted job had several attempts. Classes designed and revisions count jobs that submitted a
draft (a run with outcome ``submitted``).
"""

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.context import UserContext
from classbeat.db import user_session

NOTES: Final = ("estimate_at_list_prices", "sdk_retries", "lost_lease_turn", "failed_stream_turn")
DESIGN_KIND: Final = "design_draft"
REVISE_KIND: Final = "revise_draft"

_SUMS = (
    "count(*) AS runs, count(DISTINCT coalesce(job_id, id)) AS jobs, "
    "coalesce(sum(input_tokens), 0) AS input_tokens, "
    "coalesce(sum(output_tokens), 0) AS output_tokens, "
    "coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens, "
    "coalesce(sum(cache_write_tokens), 0) AS cache_write_tokens, "
    "coalesce(sum(cost_micros), 0) AS cost_micros"
)
_JOB = text(
    f"SELECT {_SUMS} FROM agent_runs WHERE owner_id = :owner_id AND job_id = :job_id"  # noqa: S608
)
_WINDOW = "owner_id = :owner_id AND started_at >= :start AND started_at < :end"
_TOTAL = text(f"SELECT {_SUMS} FROM agent_runs WHERE {_WINDOW}")  # noqa: S608
_BY = text(
    f"SELECT job_kind, model, {_SUMS} FROM agent_runs WHERE {_WINDOW} "  # noqa: S608
    "GROUP BY job_kind, model ORDER BY job_kind, model"
)
_SUBMITTED = text(
    "SELECT job_kind, count(DISTINCT coalesce(job_id, id)) AS n FROM agent_runs "  # noqa: S608
    f"WHERE {_WINDOW} AND outcome = 'submitted' "
    f"AND job_kind IN ('{DESIGN_KIND}', '{REVISE_KIND}') GROUP BY job_kind"
)


@dataclass(frozen=True, slots=True)
class Sums:
    runs: int
    jobs: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_micros: int


@dataclass(frozen=True, slots=True)
class JobUsage:
    """Tokens and cost summed over a job's runs and attempts."""

    runs: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_micros: int


@dataclass(frozen=True, slots=True)
class KindModel:
    job_kind: str
    model: str
    sums: Sums


@dataclass(frozen=True, slots=True)
class MonthUsage:
    month: str
    total: Sums
    by_kind_model: list[KindModel]
    classes_designed: int
    revisions: int
    notes: tuple[str, ...] = NOTES


def month_bounds(month: str) -> tuple[datetime, datetime] | None:
    """The UTC start of ``YYYY-MM`` and of the month after, or ``None`` if it isn't one."""
    if len(month) != 7 or month[4] != "-" or not (month[:4] + month[5:]).isascii():
        return None
    if not (month[:4] + month[5:]).isdigit():
        return None
    year, number = int(month[:4]), int(month[5:])
    if not (2000 <= year <= 9000 and 1 <= number <= 12):
        return None
    after = (year + 1, 1) if number == 12 else (year, number + 1)
    return datetime(year, number, 1, tzinfo=UTC), datetime(*after, 1, tzinfo=UTC)


def current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


async def job_usage(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, job_id: UUID
) -> JobUsage | None:
    """The job's summed usage, or ``None`` when no run was recorded (it never called a model)."""
    async with user_session(factory, ctx) as db:
        row = (await db.execute(_JOB, {"owner_id": ctx.user_id, "job_id": job_id})).one()
    if row.runs == 0:
        return None
    return JobUsage(
        runs=int(row.runs),
        input_tokens=int(row.input_tokens),
        output_tokens=int(row.output_tokens),
        cache_read_tokens=int(row.cache_read_tokens),
        cache_write_tokens=int(row.cache_write_tokens),
        cost_micros=int(row.cost_micros),
    )


def _sums(row: object) -> Sums:
    return Sums(**{f.name: int(getattr(row, f.name)) for f in fields(Sums)})


async def _db_month_usage(db: AsyncSession, ctx: UserContext, month: str) -> MonthUsage | None:
    bounds = month_bounds(month)
    if bounds is None:
        return None
    params = {"owner_id": ctx.user_id, "start": bounds[0], "end": bounds[1]}
    total = (await db.execute(_TOTAL, params)).one()
    rows = (await db.execute(_BY, params)).all()
    submitted = {r.job_kind: int(r.n) for r in (await db.execute(_SUBMITTED, params)).all()}
    return MonthUsage(
        month=month,
        total=_sums(total),
        by_kind_model=[KindModel(r.job_kind, r.model, _sums(r)) for r in rows],
        classes_designed=submitted.get(DESIGN_KIND, 0),
        revisions=submitted.get(REVISE_KIND, 0),
    )


async def month_usage(
    factory: async_sessionmaker[AsyncSession], ctx: UserContext, month: str
) -> MonthUsage | None:
    """The user's usage in a UTC month, or ``None`` if ``month`` is not ``YYYY-MM``."""
    async with user_session(factory, ctx) as db:
        return await _db_month_usage(db, ctx, month)
