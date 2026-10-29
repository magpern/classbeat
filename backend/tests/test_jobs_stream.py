"""The SSE stream and ``GET /api/jobs/{id}/events`` (ADR 0005 section 6): replay and
``Last-Event-ID``, live wake-up, polling, limits, and that only the owner's events leave."""

import asyncio
import contextlib
import uuid
from typing import Any

import pytest
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat.jobs import fenced_session, stream, transitions
from classbeat.jobs.events_routes import MAX_STREAMS_PER_USER, session_probe
from classbeat.jobs.hub import EventHub
from ctx import make_context
from eventtools import (
    NOW,
    REVISION,
    SECRET,
    add_event,
    chunks,
    collect,
    finish,
    lease_of_env,
    login,
    parse,
    seeded,
    signed_in,
    until,
)
from jobcore import JobsEnv
from jobtools import new_job


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


def count_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the stream's reads of the database: a second read means it is now waiting."""
    reads: list[int] = []
    real = stream.read_page

    async def counting(*args: Any, **kwargs: Any) -> Any:
        reads.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(stream, "read_page", counting)
    return reads


async def test_a_live_event_reaches_the_stream_by_notification_not_by_polling(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)
    lease = await lease_of_env(env)
    hub: EventHub = env.app.state.event_hub
    await until(lambda: hub.listening)  # so the stream waits for a wake-up, 15 s per round
    reads = count_reads(monkeypatch)
    received: list[str] = []

    async def consume() -> None:
        async for item in stream.event_stream(
            env.factory, hub, make_context(owner), job_id, 0, still_signed_in=signed_in
        ):
            received.append(item)

    task = asyncio.ensure_future(consume())
    try:
        await until(lambda: len(received) == 1)  # caught up: the next thing it does is wait
        assert len(reads) == 1
        assert [k for _, k, _ in parse(received)] == ["claimed"]
        async with fenced_session(env.factory, lease) as fs:
            await transitions.set_stage(fs, "pick")
        await until(lambda: len(received) == 2, 3)  # far sooner than keepalive or polling
        assert [k for _, k, _ in parse(received)] == ["claimed", "stage_started"]
        async with fenced_session(env.factory, lease) as fs:
            await transitions.complete(fs, None)
        await asyncio.wait_for(task, 5)
        assert [k for _, k, _ in parse(received)][-1] == "completed"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_with_the_listener_down_the_stream_polls(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stream, "POLL_SECONDS", 0.1)
    hub = EventHub(env.app.state.engine)  # never started: not listening
    assert not hub.listening
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)
    reads = count_reads(monkeypatch)
    task = asyncio.ensure_future(collect(env, owner, job_id, hub=hub))
    try:
        # Nothing wakes the stream, and keepalive is 15 s away: only polling reads again.
        await until(lambda: len(reads) >= 3, 3)
        add_event(jobs_admin, owner, job_id, "completed")
        finish(jobs_admin, job_id)
        assert [k for _, k, _ in parse(await asyncio.wait_for(task, 5))] == ["completed"]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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


@pytest.mark.parametrize("stored", ["5", '"text"', "[1]", "true"])
async def test_a_stored_scalar_payload_is_sent_empty_and_the_stream_goes_on(
    env: Env, jobs_admin: Connection, stored: str
) -> None:
    owner = await env.user()
    job_id = new_job(jobs_admin, owner)
    add_event(jobs_admin, owner, job_id, "stage_started", raw=stored)
    add_event(jobs_admin, owner, job_id, "cancelled")
    frames = await collect(env, owner, job_id)
    assert [(k, p) for _, k, p in parse(frames)] == [("stage_started", {}), ("cancelled", {})]


async def test_the_stream_ends_when_the_user_really_signs_out(
    env: Env, jobs_admin: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real session probe, with a real session row: valid, then revoked."""
    monkeypatch.setattr(stream, "KEEPALIVE_SECONDS", 0.1)
    owner = await env.user("a@example.com")
    other = await env.user("b@example.com")
    session = await env.login(owner)
    probe = session_probe(env.factory, env.settings, session.token, owner)
    assert await probe()
    assert not await session_probe(env.factory, env.settings, session.token, other)()
    assert not await session_probe(env.factory, env.settings, "no-such-token", owner)()
    disabled = await env.user("c@example.com")
    disabled_session = await env.login(disabled)
    disabled_probe = session_probe(env.factory, env.settings, disabled_session.token, disabled)
    assert await disabled_probe()
    await env.run("UPDATE users SET disabled_at = now() WHERE id = :i", i=disabled)
    assert not await disabled_probe()  # a disabled user's stream ends too
    job_id = new_job(jobs_admin, owner)
    task = asyncio.ensure_future(collect(env, owner, job_id, still_signed_in=probe))
    done, _ = await asyncio.wait({task}, timeout=0.5)  # open and idle while the session lives
    assert not done
    await env.run("DELETE FROM sessions WHERE id = :i", i=session.id)  # sign-out elsewhere
    frames = await asyncio.wait_for(task, 5)
    assert set(frames) <= {stream.KEEPALIVE_FRAME}


# --- the endpoint -----------------------------------------------------------------------------


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
    # After the end, a reconnect is a 204: an EventSource stops reconnecting (#116 item 4).
    over = await env.client.get(url, headers={**cookie, "Last-Event-ID": "4"})
    assert (over.status_code, over.text) == (204, "")
    assert (await env.client.get(url + "?after=99", headers=cookie)).status_code == 204


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


async def test_rls_alone_hides_another_owners_job_and_events(jenv: JobsEnv) -> None:
    """#116 item 1: the reads of ``progress`` without their ``owner_id`` filter, in the other
    owner's user session, still see nothing: RLS refuses on its own (and the same statements
    see the rows in the owner's session, so the query itself works)."""
    from sqlalchemy import text

    from classbeat.db import user_session

    owner, job_id = jenv.job()
    other = jenv.user()
    jenv.sql(
        "INSERT INTO job_events (owner_id, job_id, seq, kind, payload, lease_version) "
        "VALUES (:o, :j, 1, 'queued', '{}', 0)",
        o=owner,
        j=job_id,
    )
    unfiltered = (
        text("SELECT state FROM jobs WHERE id = :job_id"),
        text("SELECT seq FROM job_events WHERE job_id = :job_id"),
    )
    seen = {}
    for who in (other, owner):
        async with user_session(jenv.factory, make_context(who)) as db:
            seen[who] = [len((await db.execute(q, {"job_id": job_id})).all()) for q in unfiltered]
    assert seen == {other: [0, 0], owner: [1, 1]}


async def test_closing_the_hub_ends_every_open_stream(env: Env, jobs_admin: Connection) -> None:
    """#116 item 3: shutdown (``EventHub.close``) ends a stream that would otherwise wait."""
    owner = await env.user()
    job_id = seeded(jobs_admin, owner)  # running: the stream would wait for more
    hub = env.app.state.event_hub
    task = asyncio.ensure_future(collect(env, owner, job_id))
    await until(lambda: job_id in hub._subscriptions)  # the stream is waiting
    hub.close()
    frames = await asyncio.wait_for(task, 5)
    assert [k for _, k, _ in parse(frames)] == ["queued", "claimed", "stage_started"]
    assert await asyncio.wait_for(collect(env, owner, job_id, after=3), 5) == []  # ends at once
