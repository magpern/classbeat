"""The admin area's Jobs view (ADR 0011 section 6, AD3 #221), on the router shell of #210.

``GET /api/admin/jobs?days=7`` (``job.list``), one read ``admin_session`` on ``classbeat_maint``:

- **counts per state**, from ``jobs.state`` through maint's column ``SELECT`` (never
  ``queue_stats()``, on which maint has no grant);
- **waiting approvals**: owner (id and email), kind, waiting since (the job's ``updated_at``,
  which nothing moves while it waits) and when it expires, soonest first;
- **recent failures by category**: failed jobs that finished within ``days``, counted per kind
  and error category, with the latest;
- **uncertain external operations**: owner (id and email), the operation's kind, since when
  (its ``updated_at``) and its job's state (``None`` once the job went with its class);
- **failed player events** (ADR 0034 section 2): kind, created, given up at, attempts and the
  last error's category (``classbeat.player.outbox.failed_events``; never the subject or ``jti``),
  kept 30 days after ``give_up_at``.

The lists stop at 200 rows; ``waiting_truncated``, ``uncertain_truncated`` and
``player_events_truncated`` say when there were more, so a full list never reads as all of them
(#227, PR #281 review).

Read only (ADR 0011 section 6: an uncertain operation stays the user's to resolve, ADR 0005
section 7). Never a job's input, stage detail, keys or hashes, an operation's idempotency key,
request hash, external id, result or request context, a playlist URL or class content: maint has
no grant on those columns (guard 11) and nothing here asks. Emails come from ``classbeat.auth``
(guard 8).
"""

from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from classbeat import auth
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router
from classbeat.player.outbox import failed_events

router = admin_router()

LIST_ROWS: Final = 200

_STATES = text("SELECT state, count(*) AS n FROM jobs GROUP BY state ORDER BY state")
_WAITING = text(
    "SELECT owner_id, kind, updated_at, expires_at FROM jobs "
    "WHERE state = 'waiting_for_approval' ORDER BY expires_at, id LIMIT :n"
)
_FAILURES = text(
    "SELECT kind, error_category, count(*) AS n, max(finished_at) AS last_at FROM jobs "
    "WHERE state = 'failed' AND finished_at >= now() - make_interval(days => :days) "
    "GROUP BY kind, error_category ORDER BY n DESC, kind, error_category"
)
_UNCERTAIN = text(
    "SELECT o.owner_id, o.kind, o.updated_at, j.state AS job_state "
    "FROM external_operations o LEFT JOIN jobs j ON j.owner_id = o.owner_id AND j.id = o.job_id "
    "WHERE o.state = 'uncertain' ORDER BY o.updated_at, o.id LIMIT :n"
)


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class StateCount(Out):
    state: str
    count: int


class WaitingApproval(Out):
    user_id: UUID
    email: str | None
    kind: str
    waiting_since: datetime
    expires_at: datetime | None


class Failure(Out):
    kind: str
    category: str
    count: int
    last_at: datetime


class UncertainOperation(Out):
    user_id: UUID
    email: str | None
    kind: str
    since: datetime
    job_state: str | None


class FailedPlayerEvent(Out):
    kind: str
    created_at: datetime
    give_up_at: datetime
    attempts: int
    last_error: str | None


class JobsOut(Out):
    days: int
    states: list[StateCount]
    waiting_approvals: list[WaitingApproval]
    # True when there were more than ``LIST_ROWS``: the list shows the first 200 only.
    waiting_truncated: bool
    failures: list[Failure]
    uncertain_operations: list[UncertainOperation]
    uncertain_truncated: bool
    failed_player_events: list[FailedPlayerEvent]
    player_events_truncated: bool


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


@router.get("/jobs", response_model=JobsOut)
async def read_jobs(
    request: Request, actor: AdminActor, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> JobsOut:
    async with admin_session(_db(request), actor, "job.list", params={"days": days}) as db:
        states = (await db.execute(_STATES)).all()
        waiting = (await db.execute(_WAITING, {"n": LIST_ROWS + 1})).all()
        failures = (await db.execute(_FAILURES, {"days": days})).all()
        uncertain = (await db.execute(_UNCERTAIN, {"n": LIST_ROWS + 1})).all()
        waiting_truncated, uncertain_truncated = (
            len(waiting) > LIST_ROWS,
            len(uncertain) > LIST_ROWS,
        )
        waiting, uncertain = waiting[:LIST_ROWS], uncertain[:LIST_ROWS]
        player_events, player_events_truncated = await failed_events(db)
        emails = await auth.account_emails(
            db, {r.owner_id for r in waiting} | {r.owner_id for r in uncertain}
        )
    return JobsOut(
        days=days,
        states=[StateCount(state=r.state, count=int(r.n)) for r in states],
        waiting_approvals=[
            WaitingApproval(
                user_id=r.owner_id,
                email=emails.get(r.owner_id),
                kind=r.kind,
                waiting_since=r.updated_at,
                expires_at=r.expires_at,
            )
            for r in waiting
        ],
        waiting_truncated=waiting_truncated,
        failures=[
            Failure(kind=r.kind, category=r.error_category, count=int(r.n), last_at=r.last_at)
            for r in failures
        ],
        uncertain_operations=[
            UncertainOperation(
                user_id=r.owner_id,
                email=emails.get(r.owner_id),
                kind=r.kind,
                since=r.updated_at,
                job_state=r.job_state,
            )
            for r in uncertain
        ],
        uncertain_truncated=uncertain_truncated,
        failed_player_events=[
            FailedPlayerEvent(
                kind=e.kind,
                created_at=e.created_at,
                give_up_at=e.give_up_at,
                attempts=e.attempts,
                last_error=e.last_error,
            )
            for e in player_events
        ],
        player_events_truncated=player_events_truncated,
    )
