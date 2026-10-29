"""Job progress (ADR 0005 section 6): payload models, ``pg_notify`` on insert and the web hub. The
SSE stream and endpoint are in ``test_jobs_stream``. Fakes only; the database is the throwaway
Postgres."""

import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat.db import user_session
from classbeat.jobs import fenced_session, queue, transitions
from classbeat.jobs import hub as hub_module
from classbeat.jobs import user as job_user
from classbeat.jobs.events import MODELS, validated_payload
from classbeat.jobs.fence import EVENT_KINDS, Lease
from classbeat.jobs.hub import EventHub
from classbeat.jobs.issuer import issue
from ctx import make_context
from eventtools import NOW, PAST, REVISION, SECRET, until
from jobcore import KIND, JobsEnv

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
        ("tool_called", {"tool": "get_music", "ok": True, "error": "not_found"}),  # ok and error
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


@pytest.mark.parametrize("stored", [5, "text", [1], True])
def test_a_stored_scalar_or_list_is_no_payload(stored: Any) -> None:
    with pytest.raises(ValueError, match="event"):
        validated_payload("stage_started", stored)


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
        poison = jenv.job(owner, attempt=3, max_attempts=3)[1]  # queue_reap's second loop
        assert await queue.reap(jenv.factory) == 2
        assert sorted(await drain(connection)) == sorted([str(job_id), str(poison)])
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


def kill_listeners(admin: Connection) -> None:
    pids = admin.execute(
        text(
            "SELECT pid FROM pg_stat_activity WHERE query = 'LISTEN job_events' "
            "AND datname = current_database()"
        )
    ).scalars()
    for pid in list(pids):
        admin.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})


async def test_the_listener_comes_up_and_reconnects_after_the_connection_dies(
    env: Env, jobs_admin: Connection
) -> None:
    hub: EventHub = env.app.state.event_hub
    await until(lambda: hub.listening)
    with hub.subscribe(uuid.uuid4()) as subscription:
        kill_listeners(jobs_admin)
        await subscription.wait(5)  # woken when the listener goes down
        await until(lambda: hub.listening, 10)  # and it is back after the first backoff


class Flaky:
    """An engine whose first connections fail, as when the database is down for a while."""

    def __init__(self, engine: Any, failures: int) -> None:
        self.engine, self.failures = engine, failures

    def connect(self) -> Any:
        if self.failures > 0:
            self.failures -= 1
            raise OSError("database down")
        return self.engine.connect()


async def test_a_good_connection_resets_the_reconnect_backoff(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_module, "RECONNECT_MIN_SECONDS", 0.2)
    hub = EventHub(Flaky(env.app.state.engine, 3))  # pyright: ignore[reportArgumentType]
    hub.start()
    try:
        await until(lambda: hub.listening, 10)  # after waits of 0.2 + 0.4 + 0.8 s
        kill_listeners(jobs_admin)
        await until(lambda: not hub.listening)
        started = asyncio.get_running_loop().time()
        await until(lambda: hub.listening, 10)
        # back after the minimum wait, not after the 1.6 s the failed attempts had built up
        assert asyncio.get_running_loop().time() - started < 1.0
    finally:
        await hub.stop()
