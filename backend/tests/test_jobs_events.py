"""Job progress (ADR 0005 section 6): payload models, ``pg_notify`` on insert, the web hub, the SSE
stream with ``Last-Event-ID``, its limits, and that only the owner's events ever leave. Fakes
only; the database is the throwaway Postgres."""

import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env, session_cookie
from classbeat.db import user_session
from classbeat.jobs import fenced_session, queue, stream, transitions
from classbeat.jobs import user as job_user
from classbeat.jobs.events import MODELS, validated_payload
from classbeat.jobs.events_routes import MAX_STREAMS_PER_USER
from classbeat.jobs.fence import EVENT_KINDS, Lease
from classbeat.jobs.hub import EventHub
from classbeat.jobs.issuer import issue
from ctx import make_context
from jobcore import KIND, JobsEnv
from jobtools import new_job

SECRET = "SECRET-TOKEN-0123456789"
REVISION = uuid.uuid4()
NOW = "2026-10-29T12:00:00+00:00"
PAST = datetime(2020, 1, 1, tzinfo=UTC)


# --- payload models ---------------------------------------------------------------------------


def test_every_event_kind_has_a_model_and_the_database_accepts_exactly_those(
    jobs_admin: Connection,
) -> None:
    definition = jobs_admin.execute(
        text(
            "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
            "WHERE c.conrelid = 'public.job_events'::regclass AND c.contype = 'c' "
            "AND pg_get_constraintdef(c.oid) LIKE '%kind%'"
        )
    ).scalar_one()
    assert set(re.findall(r"'([a-z_]+)'::text", definition)) == set(MODELS) == set(EVENT_KINDS)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("queued", {}),
        ("claimed", {"attempt": 2}),
        ("stage_started", {"stage": "pick"}),
        ("tool_called", {"tool": "get_music", "ok": True}),
        ("tool_called", {"tool": "get_music", "ok": False, "error": "not_found"}),
        ("revision_created", {"revision_id": REVISION, "kind": "draft", "number": 1}),
        ("waiting_for_approval", {"revision_id": str(REVISION), "expires_at": NOW}),
        ("approved", {}),
        ("retry_scheduled", {"category": "rate_limited", "available_at": NOW}),
        ("cancel_requested", {}),
        ("cancelled", {}),
        ("completed", {"revision_id": REVISION}),
        ("completed", {}),
        ("failed", {"category": "uncertain_write"}),
        ("expired", {}),
        ("requeued", {}),
        ("external_operation", {"kind": "mowl_attach_spotify_playlist", "state": "uncertain"}),
    ],
)
def test_valid_payloads_are_accepted_and_json_ready(kind: str, payload: dict[str, Any]) -> None:
    json.dumps(validated_payload(kind, payload))


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("nope", {}),
        ("queued", {"token": SECRET}),  # extra field
        ("claimed", {"attempt": 1, "worker": SECRET}),
        ("claimed", {"attempt": 0}),
        ("stage_started", {"stage": "Pick the songs"}),  # a text, not a name
        ("stage_started", {"stage": SECRET}),
        ("tool_called", {"tool": "get_music", "ok": False, "error": f"boom {SECRET}"}),
        ("tool_called", {"tool": "get_music", "ok": True, "args": {"q": SECRET}}),
        ("tool_called", {"tool": "x" * 41, "ok": True}),
        ("revision_created", {"revision_id": REVISION, "kind": "draft"}),  # number missing
        ("revision_created", {"revision_id": "not-a-uuid", "kind": "draft", "number": 1}),
        ("waiting_for_approval", {"revision_id": REVISION}),
        ("retry_scheduled", {"category": "boom", "available_at": NOW}),
        ("failed", {"category": SECRET}),
        ("failed", {"category": "internal", "message": SECRET}),
        ("failed", {"category": {"nested": 1}}),
        ("external_operation", {"kind": "mowl_attach_spotify_playlist", "state": "weird"}),
        ("external_operation", {"kind": "https://x.test/playlist/1", "state": "pending"}),
    ],
)
def test_other_payloads_are_refused_without_echoing_them(
    kind: str, payload: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="event") as error:
        validated_payload(kind, payload)
    assert SECRET not in str(error.value)


def test_none_fields_are_left_out() -> None:
    assert validated_payload("tool_called", {"tool": "x", "ok": True, "error": None}) == {
        "tool": "x",
        "ok": True,
    }


# --- pg_notify on insert ----------------------------------------------------------------------


@contextlib.asynccontextmanager
async def listener(database_url: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    url = database_url.replace("postgresql+psycopg://", "postgresql://")
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
        await connection.execute("LISTEN job_events")
        yield connection


async def drain(connection: psycopg.AsyncConnection[Any]) -> list[str]:
    return [n.payload async for n in connection.notifies(timeout=0.5)]


async def lease_of(env: JobsEnv) -> Lease:
    row = await queue.claim(env.factory, "test-w", env.settings.job_lease_seconds, [KIND])
    assert row is not None
    return (await issue(env.factory, row)).lease


async def test_every_event_insert_notifies_with_the_job_id_only(
    jenv: JobsEnv, database_url: str
) -> None:
    owner, job_id = jenv.job()
    ctx = make_context(owner)
    async with listener(database_url) as connection:
        # the queue function's own 'claimed' event
        lease = await lease_of(jenv)
        assert await drain(connection) == [str(job_id)]
        # a worker event
        async with fenced_session(jenv.factory, lease) as fs:
            await transitions.set_stage(fs, "pick")
        assert await drain(connection) == [str(job_id)]
        # a rolled-back event wakes nobody
        with pytest.raises(RuntimeError):  # noqa: PT012
            async with fenced_session(jenv.factory, lease) as fs:
                await fs.event("stage_started", {"stage": "design"})
                raise RuntimeError("abort")
        assert await drain(connection) == []
        # a user event
        async with user_session(jenv.factory, ctx) as db:
            assert await job_user.cancel(db, ctx, job_id) == "running"
        assert await drain(connection) == [str(job_id)]
        # the reaper's and the expiry's events
        jenv.expire_lease(job_id)
        assert await queue.reap(jenv.factory) == 1
        assert await drain(connection) == [str(job_id)]
        _, waiting = jenv.job(
            owner, state="waiting_for_approval", approval_hash=b"\0" * 32, expires_at=PAST
        )
        assert await queue.expire(jenv.factory) == 1
        assert await drain(connection) == [str(waiting)]


async def test_every_event_the_queue_functions_write_matches_its_model(jenv: JobsEnv) -> None:
    owner, claimed = jenv.job()
    _, poison = jenv.job(owner, attempt=3, max_attempts=3)
    _, waiting = jenv.job(
        owner, state="waiting_for_approval", approval_hash=b"\0" * 32, expires_at=PAST
    )
    lapsed: dict[str, Any] = {
        "state": "running",
        "leased_by": "w",
        "lease_expires_at": PAST,
        "heartbeat_at": PAST,
    }
    _, spent = jenv.job(owner, attempt=3, max_attempts=3, **lapsed)
    _, retried = jenv.job(owner, attempt=1, max_attempts=3, **lapsed)
    _, cancelling = jenv.job(owner, attempt=1, **lapsed)
    jenv.sql("UPDATE jobs SET cancel_requested_at = now() WHERE id = :i", i=cancelling)
    await lease_of(jenv)
    assert await queue.reap(jenv.factory) >= 4
    assert await queue.expire(jenv.factory) == 1
    seen = set()
    for job_id in (claimed, poison, waiting, spent, retried, cancelling):
        for _, kind, payload, _ in jenv.events(job_id):
            validated_payload(kind, payload)
            seen.add(kind)
    assert {"claimed", "failed", "expired", "retry_scheduled", "cancelled"} <= seen


# --- the hub ----------------------------------------------------------------------------------


async def test_a_notification_wakes_only_streams_of_that_job(env: Env) -> None:
    hub = EventHub(env.app.state.engine)
    one, two = uuid.uuid4(), uuid.uuid4()
    with hub.subscribe(one) as first, hub.subscribe(two) as second:
        hub.notify(str(one))
        hub.notify("not a job id")
        hub.notify("")
        assert first.wake.is_set()
        assert not second.wake.is_set()
        await first.wait(0.01)
        assert not first.wake.is_set()
    hub.notify(str(one))  # nobody is subscribed: nothing happens


async def test_the_listener_comes_up_and_reconnects_after_the_connection_dies(
    env: Env, jobs_admin: Connection
) -> None:
    hub: EventHub = env.app.state.event_hub
    for _ in range(100):
        if hub.listening:
            break
        await asyncio.sleep(0.05)
    assert hub.listening
    with hub.subscribe(uuid.uuid4()) as subscription:
        pids = (
            jobs_admin.execute(
                text(
                    "SELECT pid FROM pg_stat_activity WHERE query = 'LISTEN job_events' "
                    "AND datname = current_database()"
                )
            )
            .scalars()
            .all()
        )
        assert pids
        for pid in pids:
            jobs_admin.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        await subscription.wait(5)  # woken when the listener goes down
        for _ in range(100):  # and it is back after the first backoff
            if hub.listening:
                break
            await asyncio.sleep(0.1)
        assert hub.listening


# --- the stream -------------------------------------------------------------------------------


def add_event(
    conn: Connection, owner: UUID, job_id: UUID, kind: str, payload: dict[str, Any] | None = None
) -> int:
    """An event written as the superuser (setup), with the next ``seq``."""
    seq = conn.execute(
        text("UPDATE jobs SET next_seq = next_seq + 1 WHERE id = :j RETURNING next_seq"),
        {"j": job_id},
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO job_events (owner_id, job_id, seq, kind, payload, lease_version) "
            "VALUES (:o, :j, :s, :k, CAST(:p AS jsonb), 0)"
        ),
        {"o": owner, "j": job_id, "s": seq, "k": kind, "p": json.dumps(payload or {})},
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


async def test_the_stream_replays_after_the_last_id_and_ends_on_the_terminal_event(
    env: Env, jobs_admin: Connection
) -> None:
    owner = await env.user()
    job_id = seeded(jobs_admin, owner)
    add_event(jobs_admin, owner, job_id, "completed", {"revision_id": str(REVISION)})
    finish(jobs_admin, job_id)
    full = parse(await collect(env, owner, job_id))
    assert [(s, k) for s, k, _ in full] == [
        (1, "queued"),
        (2, "claimed"),
        (3, "stage_started"),
        (4, "completed"),
    ]
    assert full[1][2] == {"attempt": 1}
    assert [s for s, _, _ in parse(await collect(env, owner, job_id, after=2))] == [3, 4]
    assert await collect(env, owner, job_id, after=4) == []  # a reconnect after the end
    assert await collect(env, owner, job_id, after=99) == []


async def test_the_stream_ends_after_waiting_for_approval_and_resumes_after_it(
    env: Env, jobs_admin: Connection
) -> None:
    owner = await env.user()
    job_id = seeded(jobs_admin, owner)
    wait = {"revision_id": str(REVISION), "expires_at": NOW}
    add_event(jobs_admin, owner, job_id, "waiting_for_approval", wait)
    add_event(jobs_admin, owner, job_id, "approved")
    assert [k for _, k, _ in parse(await collect(env, owner, job_id))][-1] == "waiting_for_approval"
    add_event(jobs_admin, owner, job_id, "cancelled")
    finish(jobs_admin, job_id, "cancelled")
    assert [k for _, k, _ in parse(await collect(env, owner, job_id, after=4))] == [
        "approved",
        "cancelled",
    ]


async def test_a_long_backlog_is_sent_in_pages_in_order(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "PAGE_SIZE", 2)
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)
    for _ in range(5):
        add_event(jobs_admin, owner, job_id, "stage_started", {"stage": "pick"})
    add_event(jobs_admin, owner, job_id, "completed")
    finish(jobs_admin, job_id)
    assert [s for s, _, _ in parse(await collect(env, owner, job_id))] == [1, 2, 3, 4, 5, 6]


async def test_a_live_event_reaches_the_stream_by_notification_not_by_polling(
    env: Env, jobs_admin: Connection
) -> None:
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)
    lease = await lease_of_env(env)
    received: list[str] = []

    async def consume() -> None:
        async for item in stream.event_stream(
            env.factory,
            env.app.state.event_hub,
            make_context(owner),
            job_id,
            0,
            still_signed_in=signed_in,
        ):
            received.append(item)

    task = asyncio.ensure_future(consume())
    try:
        for _ in range(100):
            if env.app.state.event_hub.listening:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        assert [k for _, k, _ in parse(received)] == ["claimed"]
        async with fenced_session(env.factory, lease) as fs:
            await transitions.set_stage(fs, "pick")  # keepalive and polling are 15 s away
        for _ in range(60):
            if len(received) == 2:
                break
            await asyncio.sleep(0.05)
        assert [k for _, k, _ in parse(received)] == ["claimed", "stage_started"]
        async with fenced_session(env.factory, lease) as fs:
            await transitions.complete(fs, None)
        await asyncio.wait_for(task, 5)
        assert [k for _, k, _ in parse(received)][-1] == "completed"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def lease_of_env(env: Env) -> Lease:
    row = await queue.claim(env.factory, "test-w", 60, [KIND])
    assert row is not None
    return (await issue(env.factory, row)).lease


async def test_with_the_listener_down_the_stream_polls(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "POLL_SECONDS", 0.1)
    hub = EventHub(env.app.state.engine)  # never started: not listening
    assert not hub.listening
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)
    task = asyncio.ensure_future(collect(env, owner, job_id, hub=hub))
    await asyncio.sleep(0.3)
    add_event(jobs_admin, owner, job_id, "completed")
    finish(jobs_admin, job_id)
    assert [k for _, k, _ in parse(await asyncio.wait_for(task, 5))] == ["completed"]


async def test_keepalive_comments_and_the_stream_time_limit(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "KEEPALIVE_SECONDS", 0.1)
    monkeypatch.setattr(stream, "MAX_STREAM_SECONDS", 0.6)
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)  # nothing happens
    frames = await asyncio.wait_for(collect(env, owner, job_id), 10)
    assert frames
    assert set(frames) == {stream.KEEPALIVE_FRAME}


async def test_the_stream_ends_when_the_session_does(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "KEEPALIVE_SECONDS", 0.1)
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)

    async def signed_out() -> bool:
        return False

    frames = await asyncio.wait_for(collect(env, owner, job_id, still_signed_in=signed_out), 10)
    assert set(frames) <= {stream.KEEPALIVE_FRAME}  # it ended (at most one comment first)


async def test_a_stream_never_serves_another_users_job(env: Env, jobs_admin: Connection) -> None:
    owner, other = await env.user("a@example.com"), await env.user("b@example.com")
    job_id = seeded(jobs_admin, owner)
    assert await collect(env, other, job_id) == []


async def test_a_stored_payload_off_its_model_never_leaves(
    env: Env, jobs_admin: Connection
) -> None:
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)
    add_event(jobs_admin, owner, job_id, "stage_started", {"stage": "pick", "token": SECRET})
    add_event(jobs_admin, owner, job_id, "tool_called", {"tool": "t", "ok": False, "error": SECRET})
    add_event(jobs_admin, owner, job_id, "failed", {"category": "internal", "detail": SECRET})
    frames = await collect(env, owner, job_id, after=0)  # failed is terminal: the stream ends
    assert SECRET not in "".join(frames)
    assert [p for _, _, p in parse(frames)] == [{}, {}, {}]


# --- the endpoint -----------------------------------------------------------------------------


async def login(env: Env, email: str = "user@example.com") -> tuple[UUID, dict[str, str]]:
    user_id = await env.user(email)
    return user_id, session_cookie((await env.login(user_id)).token)


async def test_the_endpoint_needs_a_session(env: Env, jobs_admin: Connection) -> None:
    owner = await env.user()
    job_id = seeded(jobs_admin, owner)
    response = await env.client.get(f"/api/jobs/{job_id}/events")
    assert response.status_code == 401


async def test_foreign_unknown_and_malformed_ids_are_the_same_404(
    env: Env, jobs_admin: Connection
) -> None:
    owner = await env.user("owner@example.com")
    job_id = seeded(jobs_admin, owner)
    add_event(jobs_admin, owner, job_id, "completed")
    finish(jobs_admin, job_id)
    _, cookie = await login(env, "other@example.com")
    bodies = set()
    for raw in (str(job_id), str(uuid.uuid4()), "not-a-uuid", "1"):
        response = await env.client.get(f"/api/jobs/{raw}/events", headers=cookie)
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/json"
        bodies.add(response.text)
    assert len(bodies) == 1
    assert "completed" not in bodies.pop()
    # not even with a Last-Event-ID or ?after=
    response = await env.client.get(
        f"/api/jobs/{job_id}/events?after=0", headers={**cookie, "Last-Event-ID": "0"}
    )
    assert response.status_code == 404


async def test_the_endpoint_replays_the_owners_events_and_resumes(
    env: Env, jobs_admin: Connection
) -> None:
    owner, cookie = await login(env)
    job_id = seeded(jobs_admin, owner)
    add_event(jobs_admin, owner, job_id, "completed", {"revision_id": str(REVISION)})
    finish(jobs_admin, job_id)
    url = f"/api/jobs/{job_id}/events"
    response = await env.client.get(url, headers=cookie)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.text.startswith("id: 1\nevent: queued\ndata: {}\n\n")
    assert response.text.endswith(
        f'id: 4\nevent: completed\ndata: {{"revision_id":"{REVISION}"}}\n\n'
    )
    resumed = await env.client.get(url, headers={**cookie, "Last-Event-ID": "3"})
    assert [s for s, _, _ in parse(chunks(resumed.text))] == [4]
    first = await env.client.get(url + "?after=2", headers=cookie)
    assert first.text.startswith("id: 3\n")
    both = await env.client.get(url + "?after=0", headers={**cookie, "Last-Event-ID": "3"})
    assert both.text.startswith("id: 4\n")  # the header wins
    assert (await env.client.get(url, headers={**cookie, "Last-Event-ID": "4"})).text == ""


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5", "99999999999999999999", " 1"])
async def test_a_malformed_last_event_id_is_a_400(
    env: Env, jobs_admin: Connection, bad: str
) -> None:
    owner, cookie = await login(env)
    job_id = seeded(jobs_admin, owner)
    response = await env.client.get(
        f"/api/jobs/{job_id}/events", headers={**cookie, "Last-Event-ID": bad}
    )
    assert response.status_code == 400
    assert response.json() == {"detail": {"reason": "bad_last_event_id"}}


async def test_at_most_five_open_streams_per_user(env: Env, jobs_admin: Connection) -> None:
    owner, cookie = await login(env)
    job_id = seeded(jobs_admin, owner)
    add_event(jobs_admin, owner, job_id, "completed")
    finish(jobs_admin, job_id)
    limiter = env.app.state.stream_limiter
    held = [limiter.acquire(owner) for _ in range(MAX_STREAMS_PER_USER)]
    assert all(held)
    url = f"/api/jobs/{job_id}/events"
    response = await env.client.get(url, headers=cookie)
    assert response.status_code == 429
    assert response.json() == {"detail": {"reason": "too_many_streams"}}
    assert "retry-after" in response.headers
    # a foreign or unknown id is a 404 even at the limit: the limit never confirms an id
    assert (
        await env.client.get(f"/api/jobs/{uuid.uuid4()}/events", headers=cookie)
    ).status_code == 404
    # another user is not affected
    other_owner, other_cookie = await login(env, "other@example.com")
    other_job = seeded(jobs_admin, other_owner)
    add_event(jobs_admin, other_owner, other_job, "completed")
    finish(jobs_admin, other_job)
    assert (
        await env.client.get(f"/api/jobs/{other_job}/events", headers=other_cookie)
    ).status_code == 200
    limiter.release(owner, held[0])
    assert (await env.client.get(url, headers=cookie)).status_code == 200
    # a finished stream gave its slot back
    assert limiter.acquire(owner) is not None  # four are still held, so one is free
    assert limiter.acquire(owner) is None


async def test_the_endpoint_never_shows_what_a_payload_model_does_not_name(
    env: Env, jobs_admin: Connection
) -> None:
    owner, cookie = await login(env)
    job_id = new_job(jobs_admin, owner)
    add_event(jobs_admin, owner, job_id, "stage_started", {"stage": "pick", "token": SECRET})
    add_event(jobs_admin, owner, job_id, "completed", {"revision_id": str(REVISION), "x": SECRET})
    finish(jobs_admin, job_id)
    response = await env.client.get(f"/api/jobs/{job_id}/events", headers=cookie)
    assert response.status_code == 200
    assert SECRET not in response.text
    assert [p for _, _, p in parse(chunks(response.text))] == [{}, {}]  # off-model: sent empty


async def test_a_user_session_of_one_job_cannot_read_another_owners_events(
    jenv: JobsEnv,
) -> None:
    """RLS under the reader: even with the right job id, the other owner's session sees nothing."""
    from classbeat.jobs.progress import read_page

    owner, job_id = jenv.job()
    other = jenv.user()
    jenv.sql(
        "INSERT INTO job_events (owner_id, job_id, seq, kind, payload, lease_version) "
        "VALUES (:o, :j, 1, 'queued', '{}', 0)",
        o=owner,
        j=job_id,
    )
    assert await read_page(jenv.factory, make_context(other), job_id, 0, 10) is None
    page = await read_page(jenv.factory, make_context(owner), job_id, 0, 10)
    assert page is not None
    assert [e.seq for e in page.events] == [1]
