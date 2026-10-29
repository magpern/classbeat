"""Shared helpers of the job-event tests (``test_jobs_events``, ``test_jobs_stream``): events
written as the superuser, SSE frame parsing, and a poll-based wait."""

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env, session_cookie
from classbeat.jobs import queue, stream
from classbeat.jobs.fence import Lease
from classbeat.jobs.issuer import issue
from ctx import make_context
from jobcore import KIND
from jobtools import new_job

SECRET = "SECRET-TOKEN-0123456789"
REVISION = uuid.uuid4()
NOW = "2026-10-29T12:00:00+00:00"
PAST = datetime(2020, 1, 1, tzinfo=UTC)


async def until(check: Callable[[], bool], seconds: float = 5.0) -> None:
    """Poll ``check`` until it holds (a wait on a condition, never a fixed sleep)."""
    deadline = asyncio.get_running_loop().time() + seconds
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


def add_event(
    conn: Connection,
    owner: UUID,
    job_id: UUID,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    raw: str | None = None,
) -> int:
    """An event written as the superuser (setup), with the next ``seq``; ``raw`` is the stored
    JSON text when a test needs something that is not an object."""
    seq = conn.execute(
        text("UPDATE jobs SET next_seq = next_seq + 1 WHERE id = :j RETURNING next_seq"),
        {"j": job_id},
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO job_events (owner_id, job_id, seq, kind, payload, lease_version) "
            "VALUES (:o, :j, :s, :k, CAST(:p AS jsonb), 0)"
        ),
        {
            "o": owner,
            "j": job_id,
            "s": seq,
            "k": kind,
            "p": raw if raw is not None else json.dumps(payload or {}),
        },
    )
    return seq


def finish(conn: Connection, job_id: UUID, state: str = "completed") -> None:
    conn.execute(
        text("UPDATE jobs SET state = :s, finished_at = now() WHERE id = :j"),
        {"s": state, "j": job_id},
    )


def chunks(body: str) -> list[str]:
    return [part + "\n\n" for part in body.split("\n\n") if part]


def parse(frames: list[str]) -> list[tuple[int, str, dict[str, Any]]]:
    out = []
    for item in frames:
        if item.startswith(":"):
            continue
        lines = item.strip("\n").split("\n")
        assert [line.split(": ", 1)[0] for line in lines] == ["id", "event", "data"]
        out.append((int(lines[0][4:]), lines[1][7:], json.loads(lines[2][6:])))
    return out


async def signed_in(_: None = None) -> bool:
    return True


async def collect(env: Env, owner: UUID, job_id: UUID, after: int = 0, **kw: Any) -> list[str]:
    hub = kw.pop("hub", env.app.state.event_hub)
    check = kw.pop("still_signed_in", signed_in)
    return [
        item
        async for item in stream.event_stream(
            env.factory, hub, make_context(owner), job_id, after, still_signed_in=check
        )
    ]


def seeded(jobs_admin: Connection, env_user: UUID) -> UUID:
    job_id = new_job(jobs_admin, env_user)
    add_event(jobs_admin, env_user, job_id, "queued")
    add_event(jobs_admin, env_user, job_id, "claimed", {"attempt": 1})
    add_event(jobs_admin, env_user, job_id, "stage_started", {"stage": "pick"})
    return job_id


async def lease_of_env(env: Env) -> Lease:
    row = await queue.claim(env.factory, "test-w", 60, [KIND])
    assert row is not None
    return (await issue(env.factory, row)).lease


async def login(env: Env, email: str = "user@example.com") -> tuple[UUID, dict[str, str]]:
    user_id = await env.user(email)
    return user_id, session_cookie((await env.login(user_id)).token)
