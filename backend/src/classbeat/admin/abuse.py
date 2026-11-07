"""The admin area's Abuse view (ADR 0003 amendment "the security log", #632 item 3), on the router
shell of #210.

``GET /api/admin/abuse?minutes=60`` (``abuse.list``; 5 to 240 minutes), one read ``admin_session``
on ``classbeat_maint`` over ``security_log`` (the privacy-safe log of #641) for the last
``minutes``:

- **top users by request rate**: requests in the window, the average and the busiest minute, the
  429s, and in how many networks and sessions the user was seen;
- **limit hits**: users with a 429 in the window (the log keeps at most one 429 row per user and
  minute, so the count is minutes in which the user was limited), with the first and last, so a
  burst reads as "N minutes within M";
- **sessions seen from several networks**: sessions whose requests came from two or more distinct
  network hashes (a keyed pseudonym of the IPv4 address, or of the IPv6 /64) in the window, with
  how many and when.

Each row names the user (id, email, role, disabled) so the page can offer the shortcut: a button
that calls the existing ``POST /api/admin/users/{id}/disable`` (it revokes every session of the
user; step-up and its own audit row are that route's). Nothing here writes.

**Data minimisation.** maint holds ``SELECT`` on five ``security_log`` columns only (``owner_id``,
``session_id``, ``ip_hash``, ``status``, ``at``; never the method or route, and guard 11 pins the
list). ``ip_hash`` is read only inside ``count(DISTINCT ...)``: no response model has a field for
a hash, an address, a session id, a User-Agent, a token, a path or a body
(``tests/test_admin_abuse.py`` pins the field names, and searches the responses for planted
values). The log does not hold the
rate-limit buckets' owners (``rate_limits`` keys are hashes of an address or account, not a user),
so those counters are not read here.

Lists stop at ``LIST_ROWS``; ``*_truncated`` says when there were more. The aggregates scan the
window of one table under maint's 30 s statement timeout.
"""

from datetime import datetime
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from classbeat import auth
from classbeat.admin.db import AdminDb, AdminRefused, admin_session
from classbeat.admin.routes import AdminActor, admin_db, admin_router

router = admin_router()

LIST_ROWS: Final = 20
# A session seen in this many networks or more is listed.
MIN_NETWORKS: Final = 2

_WINDOW = "at >= now() - make_interval(mins => :minutes)"
_TOTALS = text(
    "SELECT count(*) AS requests, count(*) FILTER (WHERE status = 429) AS limited, "  # noqa: S608
    f"count(DISTINCT owner_id) AS owners FROM security_log WHERE {_WINDOW}"
)
# Per user and minute first, so the busiest minute is a max over small groups.
_TOP = text(
    "WITH per_minute AS (SELECT owner_id, date_trunc('minute', at) AS minute, count(*) AS n "  # noqa: S608
    f"FROM security_log WHERE {_WINDOW} GROUP BY 1, 2), "
    "per_user AS (SELECT owner_id, sum(n) AS requests, max(n) AS busiest FROM per_minute "
    "GROUP BY 1), "
    "seen AS (SELECT owner_id, count(DISTINCT ip_hash) AS networks, "
    "count(DISTINCT session_id) AS session_count, count(*) FILTER (WHERE status = 429) AS limited "
    f"FROM security_log WHERE {_WINDOW} GROUP BY 1) "
    "SELECT p.owner_id, p.requests, p.busiest, s.networks, s.session_count, s.limited "
    "FROM per_user p JOIN seen s USING (owner_id) ORDER BY p.requests DESC, p.owner_id LIMIT :n"
)
_LIMITED = text(
    "SELECT owner_id, count(*) AS hits, min(at) AS first_at, max(at) AS last_at "  # noqa: S608
    f"FROM security_log WHERE status = 429 AND {_WINDOW} GROUP BY owner_id "
    "ORDER BY hits DESC, last_at DESC, owner_id LIMIT :n"
)
_MOVED = text(
    "SELECT owner_id, count(DISTINCT ip_hash) AS networks, count(*) AS requests, "  # noqa: S608
    f"min(at) AS first_at, max(at) AS last_at FROM security_log WHERE {_WINDOW} "
    "GROUP BY owner_id, session_id HAVING count(DISTINCT ip_hash) >= :min_networks "
    "ORDER BY networks DESC, last_at DESC, owner_id LIMIT :n"
)


class Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class Subject(Out):
    """The user a row is about; ``role`` and ``disabled`` decide whether the shortcut is offered
    (the disable route refuses an admin)."""

    user_id: UUID
    email: str | None
    role: str | None
    disabled: bool


class TopUser(Subject):
    requests: int
    per_minute: float
    busiest_minute: int
    rate_limited: int
    networks: int
    sessions: int


class LimitHit(Subject):
    hits: int
    first_at: datetime
    last_at: datetime


class MovedSession(Subject):
    networks: int
    requests: int
    first_at: datetime
    last_at: datetime


class AbuseOut(Out):
    minutes: int
    requests: int
    rate_limited: int
    users: int
    top_users: list[TopUser]
    limit_hits: list[LimitHit]
    moved_sessions: list[MovedSession]
    # True when more rows matched than are listed (``LIST_ROWS``).
    top_users_truncated: bool
    limit_hits_truncated: bool
    moved_sessions_truncated: bool


def _db(request: Request) -> AdminDb:
    db = admin_db(request)
    if db is None:  # the gate answered 503 already; this keeps the type honest
        raise AdminRefused("admin_unavailable", 503)
    return db


@router.get("/abuse", response_model=AbuseOut)
async def read_abuse(
    request: Request, actor: AdminActor, minutes: Annotated[int, Query(ge=5, le=240)] = 60
) -> AbuseOut:
    window = {"minutes": minutes}
    async with admin_session(_db(request), actor, "abuse.list", params=window) as db:
        totals = (await db.execute(_TOTALS, window)).one()
        extra = {"n": LIST_ROWS + 1}
        top = (await db.execute(_TOP, {**window, **extra})).all()
        limited = (await db.execute(_LIMITED, {**window, **extra})).all()
        moved = (await db.execute(_MOVED, {**window, **extra, "min_networks": MIN_NETWORKS})).all()
        flags = (len(top) > LIST_ROWS, len(limited) > LIST_ROWS, len(moved) > LIST_ROWS)
        top, limited, moved = top[:LIST_ROWS], limited[:LIST_ROWS], moved[:LIST_ROWS]
        states = await auth.account_states(
            db, {r.owner_id for rows in (top, limited, moved) for r in rows}
        )

    def who(owner_id: UUID) -> dict[str, Any]:
        state = states.get(owner_id)
        return {
            "user_id": owner_id,
            "email": state.email if state else None,
            "role": state.role if state else None,
            "disabled": state is not None and state.disabled_at is not None,
        }

    return AbuseOut(
        minutes=minutes,
        requests=int(totals.requests),
        rate_limited=int(totals.limited),
        users=int(totals.owners),
        top_users=[
            TopUser(
                **who(r.owner_id),
                requests=int(r.requests),
                per_minute=round(int(r.requests) / minutes, 2),
                busiest_minute=int(r.busiest),
                rate_limited=int(r.limited),
                networks=int(r.networks),
                sessions=int(r.session_count),
            )
            for r in top
        ],
        limit_hits=[
            LimitHit(
                **who(r.owner_id),
                hits=int(r.hits),
                first_at=r.first_at,
                last_at=r.last_at,
            )
            for r in limited
        ],
        moved_sessions=[
            MovedSession(
                **who(r.owner_id),
                networks=int(r.networks),
                requests=int(r.requests),
                first_at=r.first_at,
                last_at=r.last_at,
            )
            for r in moved
        ],
        top_users_truncated=flags[0],
        limit_hits_truncated=flags[1],
        moved_sessions_truncated=flags[2],
    )
