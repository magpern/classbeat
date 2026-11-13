"""Comparing a request's client mark with its session's (ADR 0003 amendment of 2026-11-05, #632).

``sessions.resolve_session`` calls ``compare_mark`` for every request that resolves a session with
a ``ClientMark``. A ``Peerless`` request (#926, #1023) never gets here: it has nothing to compare.
"""

from typing import Any

from sqlalchemy import case, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from classbeat.auth.anomaly import EVENT_INTERVAL, EVENT_KIND, ClientMark
from classbeat.auth.events import record_event
from classbeat.auth.tables import sessions


def _differs(stored_hash: bytes | None, stored_agent: str | None, mark: ClientMark) -> bool:
    return (stored_hash is not None and bytes(stored_hash) != mark.ip_hash) or (
        stored_agent is not None and stored_agent != mark.agent
    )


async def compare_mark(db: AsyncSession, row: Any, mark: ClientMark, ip: str | None) -> bool:
    """Compare the request's mark with the session's (#632). Returns whether it moved.

    No stored mark (a session from before migration 0033, or one a peerless request started):
    adopt this one silently. A different one: store it, clear ``reauth_at``, ``mfa_at`` and
    ``stepup_at``, set ``location_changed_at``, and write a ``session_location_changed`` event
    when ``location_event_at`` (the last event) is unset or at least ``EVENT_INTERVAL`` old. That
    choice is made inside the conditional update, on the locked row: of parallel moves (with the
    same or different marks) only one sets ``location_event_at`` and writes the event, and moves
    that keep coming less than a minute apart still write one event a minute (the interval runs
    from the last event, not the last move). Never a sign-out.
    """
    if row.seen_ip_hash is None or row.seen_agent is None:
        await db.execute(
            update(sessions)
            .where(
                sessions.c.id == row.id,
                or_(sessions.c.seen_ip_hash.is_(None), sessions.c.seen_agent.is_(None)),
            )
            .values(seen_ip_hash=mark.ip_hash, seen_agent=mark.agent)
        )
        return False
    if not _differs(row.seen_ip_hash, row.seen_agent, mark):
        return False
    due = or_(
        sessions.c.location_event_at.is_(None),
        sessions.c.location_event_at <= func.now() - EVENT_INTERVAL,
    )
    changed = (
        await db.execute(
            update(sessions)
            .where(
                sessions.c.id == row.id,
                or_(
                    sessions.c.seen_ip_hash.is_distinct_from(mark.ip_hash),
                    sessions.c.seen_agent.is_distinct_from(mark.agent),
                ),
            )
            .values(
                seen_ip_hash=mark.ip_hash,
                seen_agent=mark.agent,
                location_changed_at=func.now(),
                location_event_at=case((due, func.now()), else_=sessions.c.location_event_at),
                reauth_at=None,
                mfa_at=None,
                stepup_at=None,
            )
            # RETURNING sees the new row: the event is this transaction's when it stamped its
            # own ``now()`` (a parallel winner's update holds an earlier transaction's time).
            .returning((sessions.c.location_event_at == func.now()).label("recorded"))
        )
    ).first()
    if changed is not None and changed.recorded:
        await record_event(
            db,
            EVENT_KIND,
            user_id=row.user_id,
            ip=ip,
            detail={
                "session_id": str(row.id),
                "ip_changed": bytes(row.seen_ip_hash) != mark.ip_hash,
                "agent_changed": row.seen_agent != mark.agent,
                "agent": mark.agent,
            },
        )
    return True
