"""The SSE stream through a real HTTP server (#116 item 2 and 3): uvicorn in a thread on a
loopback port, so frames arrive as they are sent (httpx's ASGI transport buffers the whole
response). A session deleted mid-stream ends the stream; SIGTERM ends open streams and the
server stops well inside its graceful-shutdown time."""

import asyncio
import signal
import threading
import time
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env, make_settings
from classbeat.__main__ import Server, server_config
from classbeat.jobs import stream
from classbeat.web import create_app
from eventtools import login, seeded

pytestmark = pytest.mark.usefixtures("env")


@pytest.fixture
def live(app_database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Server, str]]:
    monkeypatch.setattr(stream, "KEEPALIVE_SECONDS", 0.3)  # the session check runs this often
    app = create_app(make_settings(app_database_url))
    server = Server(server_config(app, "127.0.0.1", 0), app)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert thread.is_alive(), "the server did not start"
        assert time.monotonic() < deadline, "the server did not start in time"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield server, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(30)
        assert not thread.is_alive()


async def _open(base: str, job_id: UUID, cookie: dict[str, str]) -> AsyncIterator[str]:
    async with (
        httpx.AsyncClient(base_url=base, timeout=10) as client,
        client.stream("GET", f"/api/jobs/{job_id}/events", headers=cookie) as response,
    ):
        assert response.status_code == 200
        async for chunk in response.aiter_text():
            yield chunk


async def _until_text(chunks: AsyncIterator[str], wanted: str) -> str:
    seen = ""
    async for chunk in chunks:
        seen += chunk
        if wanted in seen:
            return seen
    raise AssertionError(f"the stream ended before {wanted!r}")


async def _rest(chunks: AsyncIterator[str]) -> str:
    return "".join([chunk async for chunk in chunks])


async def test_a_session_ended_mid_stream_ends_the_live_stream(
    env: Env, jobs_admin: Connection, live: tuple[Server, str]
) -> None:
    _, base = live
    owner, cookie = await login(env)
    job_id = seeded(jobs_admin, owner)  # running: the stream waits for more
    chunks = _open(base, job_id, cookie)
    await asyncio.wait_for(_until_text(chunks, "event: stage_started"), 10)
    # Still open across several session checks while the session is valid.
    await asyncio.wait_for(_until_text(chunks, ": keepalive"), 5)
    await asyncio.wait_for(_until_text(chunks, ": keepalive"), 5)
    jobs_admin.execute(text("DELETE FROM sessions WHERE user_id = :u"), {"u": owner})
    started = time.monotonic()
    await asyncio.wait_for(_rest(chunks), 5)  # the server ended it
    assert time.monotonic() - started < 3


async def test_sigterm_ends_open_streams_and_the_server_stops(
    env: Env, jobs_admin: Connection, live: tuple[Server, str]
) -> None:
    server, base = live
    owner, cookie = await login(env)
    job_id = seeded(jobs_admin, owner)
    chunks = _open(base, job_id, cookie)
    await asyncio.wait_for(_until_text(chunks, "event: stage_started"), 10)
    started = time.monotonic()
    server.handle_exit(signal.SIGTERM, None)  # what uvicorn's signal handler calls
    await asyncio.wait_for(_rest(chunks), 5)
    assert server.should_exit
    # The stream ended on the hub's signal, not on the graceful-shutdown timeout.
    assert time.monotonic() - started < 3
