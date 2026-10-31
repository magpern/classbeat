"""Helpers for the admin Usage, Jobs and refund tests (AD3 #221): an admin with a fresh step-up,
the audit rows of an actor, and superuser plants of classes, jobs, runs, ledger rows and external
operations (the superuser bypasses row security; nothing here goes through the app)."""

import json
import secrets
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env

MONTH = "date_trunc('month', now() AT TIME ZONE 'UTC')"
PERIOD = f"CAST({MONTH} AS date)"
LAST_MONTH_RUN = f"({MONTH} - interval '1 day') AT TIME ZONE 'UTC'"


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


async def stepped_admin(env: Env, make_actor: Any, email: str = "a@example.com") -> Any:
    """An admin with TOTP in the last 12 hours and a fresh step-up."""
    user_id = await env.user(email, role="admin")
    actor = await make_actor(user_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


def audit(conn: Connection, actor_id: object) -> list[tuple[str, str, str | None]]:
    """``(action, outcome, reason)`` of ``actor_id``'s audit rows, oldest first."""
    return [
        (r.action, r.outcome, r.reason)
        for r in conn.execute(
            text(
                "SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :u "
                "ORDER BY created_at, id"
            ),
            {"u": actor_id},
        )
    ]


def new_class(conn: Connection, owner: uuid.UUID, brief: str | None = None) -> uuid.UUID:
    class_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO classes (id, owner_id, title, brief) "
            "VALUES (:c, :o, 'Ride', CAST(:b AS jsonb))"
        ),
        {"c": class_id, "o": owner, "b": json.dumps({"text": brief or "a ride"})},
    )
    return class_id


def new_job(
    conn: Connection,
    owner: uuid.UUID,
    kind: str,
    state: str,
    *,
    credit_state: str = "none",
    category: str | None = None,
    finished_days_ago: int = 0,
    job_input: str = "an input",
    class_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """A job in ``state`` with the columns its constraints need, in a class of its own."""
    extra: dict[str, Any] = {}
    if state == "waiting_for_approval":
        extra = {"approval_hash": secrets.token_bytes(32)}
    columns = ", ".join(extra)
    values = ", ".join(f":{c}" for c in extra)
    return conn.execute(
        text(
            "INSERT INTO jobs (owner_id, kind, state, class_id, input, idempotency_key, "  # noqa: S608
            "request_hash, credit_state, error_category, expires_at, leased_by, "
            "lease_expires_at, finished_at"
            + (f", {columns}" if columns else "")
            + ") VALUES (:o, :k, :s, :c, CAST(:i AS jsonb), :key, :h, :cs, :cat, "
            "CASE WHEN :s = 'waiting_for_approval' THEN now() + interval '7 days' END, "
            "CASE WHEN :s = 'running' THEN 'worker-1' END, "
            "CASE WHEN :s = 'running' THEN now() + interval '1 minute' END, "
            "CASE WHEN :s IN ('completed', 'failed', 'cancelled', 'expired') "
            "THEN now() - make_interval(days => :ago) END"
            + (f", {values}" if values else "")
            + ") RETURNING id"
        ),
        {
            "o": owner,
            "k": kind,
            "s": state,
            "c": class_id or new_class(conn, owner),
            "i": json.dumps({"x": job_input}),
            "key": secrets.token_urlsafe(18),
            "h": secrets.token_bytes(32),
            "cs": credit_state,
            "cat": category,
            "ago": finished_days_ago,
            **extra,
        },
    ).scalar_one()


def new_run(
    conn: Connection,
    owner: uuid.UUID,
    job: uuid.UUID | None,
    kind: str,
    cost: int,
    *,
    model: str = "fake-model",
    tokens: int = 10,
    last_month: bool = False,
) -> None:
    started = LAST_MONTH_RUN if last_month else "now()"
    conn.execute(
        text(
            "INSERT INTO agent_runs (owner_id, job_id, stage, attempt, provider, model, effort, "  # noqa: S608
            "prompt_version, input_tokens, output_tokens, cost_micros, job_kind, price_version, "
            f"started_at) VALUES (:o, :j, 'design', 1, 'fake', :m, 'medium', 'v1', :t, :t, :c, "
            f":k, '2026-10-29', {started})"
        ),
        {"o": owner, "j": job, "m": model, "t": tokens, "c": cost, "k": kind},
    )


def new_ledger(
    conn: Connection,
    owner: uuid.UUID,
    job: uuid.UUID | None,
    state: str,
    *,
    plan: str = "free",
    reason: str | None = None,
) -> uuid.UUID:
    """A ledger row of this month's period (the superuser is bound by no policy; the trigger
    guards updates only)."""
    return conn.execute(
        text(
            "INSERT INTO credit_ledger (owner_id, job_id, period, plan_id, state, reason, "  # noqa: S608
            f"settled_at) VALUES (:o, :j, {PERIOD}, :p, :s, :r, "
            "CASE WHEN :s <> 'reserved' THEN now() END) RETURNING id"
        ),
        {"o": owner, "j": job, "p": plan, "s": state, "r": reason},
    ).scalar_one()


def ledger_row(conn: Connection, ledger_id: uuid.UUID) -> Any:
    return conn.execute(
        text("SELECT state, reason, settled_at, refunded_by FROM credit_ledger WHERE id = :l"),
        {"l": ledger_id},
    ).one()
