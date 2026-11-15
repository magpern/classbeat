"""The ``player_events`` outbox (ADR 0034 section 2): the only module that reads or writes it.

**Writers** run in the transaction of the change, with the pairwise subjects
``classbeat.auth`` computed for a user whose ``oidc_grants.first_issued_at`` is set (nobody
else's change leaves classbeat):

- ``record_sessions_revoked``: one row per subject, skipped while one for the same ``sub`` is
  still ``pending`` and not leased (the effect is the same);
- ``record_account_deleted``: deletes the subject's pending ``sessions_revoked`` rows and writes
  one ``account_deleted`` row, before the ``users`` row goes.

Each row gets its own ``jti`` (32 random bytes, base64url), kept for every attempt.

**Delivery** borrows ADR 0005 section 3's lease and fence: ``claim`` takes one due row with
``FOR UPDATE SKIP LOCKED``, adds 1 to ``attempt`` and ``lease_version`` and leases it for 60
seconds to one worker; ``delivered``, ``retry`` and ``give_up`` write only under that same
``lease_version`` and worker, so a stale worker changes nothing. ``maintain`` requeues expired
leases (``lease_version + 1`` again), marks rows past ``give_up_at`` or their 40 attempts
``failed``, and purges failed rows 30 days after their ``give_up_at`` (so at least 30 days after
they failed). ``failed_events`` lists the failed rows for the admin area (no ``sub``, no ``jti``).

The admin area's erase and disable run these writers on ``classbeat_maint``, whose grants
(``docker/postgres/maint/30-maint.sql``) allow exactly the statements here: no ``RETURNING`` and
no read of ``jti`` in the writers.
"""

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import and_, delete, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ColumnElement, Executable

from classbeat.player.event_policy import (
    FAILED_KEPT,
    JTI_BYTES,
    LEASE,
    MAX_ATTEMPTS,
    ErrorCategory,
    EventKind,
)
from classbeat.player.schema import player_events as t

FAILED_LIST_ROWS: Final = 200


def new_jti() -> str:
    return secrets.token_urlsafe(JTI_BYTES)


async def record_sessions_revoked(db: AsyncSession, subjects: Iterable[str]) -> None:
    for sub in subjects:
        # ``SELECT id``, never ``SELECT *``: maint may not read every column (no ``jti``).
        waiting = (
            select(t.c.id)
            .where(
                t.c.sub == sub,
                t.c.kind == "sessions_revoked",
                t.c.state == "pending",
                t.c.leased_by.is_(None),
            )
            .exists()
        )
        await db.execute(
            t.insert().from_select(
                ["kind", "sub", "jti"],
                select(literal("sessions_revoked"), literal(sub), literal(new_jti())).where(
                    ~waiting
                ),
            )
        )


async def record_account_deleted(db: AsyncSession, subjects: Iterable[str]) -> None:
    for sub in subjects:
        await db.execute(
            delete(t).where(t.c.sub == sub, t.c.kind == "sessions_revoked", t.c.state == "pending")
        )
        await db.execute(t.insert().values(kind="account_deleted", sub=sub, jti=new_jti()))


@dataclass(frozen=True, slots=True)
class Claim:
    id: UUID
    kind: EventKind
    sub: str
    jti: str
    attempt: int
    lease_version: int
    worker: str
    give_up_at: datetime

    def __repr__(self) -> str:  # never the subject or the jti
        return f"Claim(id={self.id}, kind={self.kind}, attempt={self.attempt})"


async def claim(db: AsyncSession, worker: str) -> Claim | None:
    """Lease one due event to ``worker`` (60 s), or ``None``."""
    now = func.now()
    due = (
        select(t.c.id)
        .where(
            t.c.state == "pending",
            t.c.leased_by.is_(None),
            t.c.available_at <= now,
            t.c.give_up_at > now,
            t.c.attempt < MAX_ATTEMPTS,
        )
        .order_by(t.c.available_at, t.c.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    row = (
        await db.execute(
            update(t)
            .where(t.c.id == due)
            .values(
                attempt=t.c.attempt + 1,
                lease_version=t.c.lease_version + 1,
                leased_by=worker,
                lease_expires_at=now + LEASE,
            )
            .returning(
                t.c.id, t.c.kind, t.c.sub, t.c.jti, t.c.attempt, t.c.lease_version, t.c.give_up_at
            )
        )
    ).first()
    if row is None:
        return None
    return Claim(
        row.id, row.kind, row.sub, row.jti, row.attempt, row.lease_version, worker, row.give_up_at
    )


def _fenced(lease: Claim) -> ColumnElement[bool]:
    return and_(
        t.c.id == lease.id,
        t.c.lease_version == lease.lease_version,
        t.c.leased_by == lease.worker,
        t.c.state == "pending",
    )


async def _changed(db: AsyncSession, statement: Executable) -> bool:
    result = await db.execute(statement)
    return bool(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]


async def delivered(db: AsyncSession, lease: Claim) -> bool:
    """The player answered ``2xx``: the row goes. ``False`` when the lease was lost."""
    return await _changed(db, delete(t).where(_fenced(lease)))


async def retry(db: AsyncSession, lease: Claim, at: datetime, error: ErrorCategory) -> bool:
    return await _changed(
        db,
        update(t)
        .where(_fenced(lease))
        .values(available_at=at, leased_by=None, lease_expires_at=None, last_error=error),
    )


async def give_up(db: AsyncSession, lease: Claim, error: ErrorCategory) -> bool:
    return await _changed(
        db,
        update(t)
        .where(_fenced(lease))
        .values(state="failed", leased_by=None, lease_expires_at=None, last_error=error),
    )


async def maintain(db: AsyncSession) -> dict[str, int]:
    """Requeue expired leases, fail what is past its time or attempts, purge old failures."""
    now = func.now()
    statements = {
        "requeued": update(t)
        .where(t.c.state == "pending", t.c.lease_expires_at <= now)
        .values(leased_by=None, lease_expires_at=None, lease_version=t.c.lease_version + 1),
        "failed": update(t)
        .where(
            t.c.state == "pending",
            t.c.leased_by.is_(None),
            or_(t.c.give_up_at <= now, t.c.attempt >= MAX_ATTEMPTS),
        )
        .values(state="failed", last_error=func.coalesce(t.c.last_error, "expired")),
        "purged": delete(t).where(t.c.state == "failed", t.c.give_up_at <= now - FAILED_KEPT),
    }
    counts: dict[str, int] = {}
    for name, statement in statements.items():
        result = await db.execute(statement)
        counts[name] = int(result.rowcount or 0)  # pyright: ignore[reportAttributeAccessIssue]
    return counts


@dataclass(frozen=True, slots=True)
class FailedEvent:
    """A failed event as the admin area shows it: never its ``sub`` or ``jti``."""

    kind: str
    created_at: datetime
    give_up_at: datetime
    attempts: int
    last_error: str | None


async def failed_events(db: AsyncSession) -> tuple[list[FailedEvent], bool]:
    """The failed events, oldest first, at most ``FAILED_LIST_ROWS``; and whether there were
    more."""
    rows = (
        await db.execute(
            select(t.c.kind, t.c.created_at, t.c.give_up_at, t.c.attempt, t.c.last_error)
            .where(t.c.state == "failed")
            .order_by(t.c.created_at, t.c.id)
            .limit(FAILED_LIST_ROWS + 1)
        )
    ).all()
    events = [
        FailedEvent(r.kind, r.created_at, r.give_up_at, r.attempt, r.last_error)
        for r in rows[:FAILED_LIST_ROWS]
    ]
    return events, len(rows) > FAILED_LIST_ROWS
