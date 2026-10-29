"""Helpers for the jobs API tests (ADR 0005 section 2): a browser with CSRF and an idempotency
key, the test-only kind as a ``JobInput`` that uses the MOWL connection, a fake MOWL connection
row (a public test token, never a real one) and waiting jobs made by running the worker side.
"""

import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

import httpx
import pytest

from authtools import ORIGIN, Env, session_cookie
from classbeat import connections
from classbeat.jobs import AwaitApproval, Complete, JobKind, JobRun, KindRegistry, Outcome
from classbeat.jobs.inputs import JobInput
from classbeat.jobs.routes import job_kinds
from classbeat.jobs.user import OWNER_LOCK_NAMESPACE
from ctx import make_context
from jobcore import KIND, SONG_LIST, JobsEnv, content_hash, run_next, store_draft, store_list

FAKE_TOKEN = "fake-mowl-token-for-tests"  # a public test value
UNKNOWN = "00000000-0000-4000-8000-000000000000"
LIST_HASH = content_hash(SONG_LIST).hex()


class ApiInput(JobInput):
    CONNECTIONS: ClassVar[tuple[str, ...]] = ("mowl",)
    note: str = "fine"
    mowl_connection_id: UUID


async def two_phases(job_run: JobRun) -> Outcome:
    """Waits once for approval with a song list; the approved phase completes with a draft."""
    if job_run.approved:
        return Complete(store_draft)
    return AwaitApproval(store_list)


def api_registry() -> KindRegistry:
    registry = KindRegistry()
    registry.register(JobKind(KIND, ApiInput, ("pick", "design"), two_phases))
    return registry


def new_key() -> str:
    return secrets.token_urlsafe(18)


@dataclass
class Browser:
    """One signed-in browser: session cookie, CSRF token per request, and the user's ids."""

    env: Env
    token: str
    client: httpx.AsyncClient
    user_id: UUID
    connection_id: UUID | None = None

    async def headers(self) -> dict[str, str]:
        info = await self.client.get("/api/auth/session", headers=session_cookie(self.token))
        self.client.cookies.clear()
        return {"Origin": ORIGIN, "X-CSRF-Token": info.json()["csrf_token"]} | session_cookie(
            self.token
        )

    async def post(
        self, path: str, json: Any = None, extra: dict[str, str] | None = None
    ) -> httpx.Response:
        headers = await self.headers() | (extra or {})
        response = await self.client.post(path, headers=headers, json=json)
        self.client.cookies.clear()
        return response

    async def get(self, path: str) -> httpx.Response:
        response = await self.client.get(path, headers=session_cookie(self.token))
        self.client.cookies.clear()
        return response

    def body(self, **input_: Any) -> dict[str, Any]:
        values: dict[str, Any] = {"mowl_connection_id": str(self.connection_id)}
        if "class_id" not in input_:
            values["title"] = "Tuesday climb"
        return {"kind": KIND, "input": values | input_}

    async def create(self, key: str | None = None, **input_: Any) -> httpx.Response:
        return await self.post(
            "/api/jobs", self.body(**input_), {"Idempotency-Key": key or new_key()}
        )

    async def approve(self, job_id: object, revision_id: object, digest: str) -> httpx.Response:
        body = {"revision_id": str(revision_id), "content_hash": digest}
        return await self.post(f"/api/jobs/{job_id}/approve", body)


@pytest.fixture
async def browsers(env: Env) -> AsyncIterator[Any]:
    env.app.dependency_overrides[job_kinds] = api_registry
    clients: list[httpx.AsyncClient] = []

    async def make(email: str | None = None, *, mowl: bool = True) -> Browser:
        user_id = await env.user(email or f"jobs-{secrets.token_hex(4)}@example.com")
        issued = await env.login(user_id)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN)
        clients.append(client)
        browser = Browser(env, issued.token, client, user_id)
        if mowl:
            browser.connection_id = await connections.save(
                env.factory, env.app.state.keyring, make_context(user_id), "mowl",
                {"token": FAKE_TOKEN},
            )  # fmt: skip
        return browser

    yield make
    env.app.dependency_overrides.clear()
    for client in clients:
        await client.aclose()


async def waiting_job(jenv: JobsEnv, browser: Browser) -> tuple[UUID, UUID]:
    """A job of ``browser`` waiting for approval (created through the API, run by the worker
    side): (job id, song-list revision id). No other test job may be queued."""
    created = await browser.create()
    assert created.status_code == 201, created.text
    job_id = UUID(created.json()["id"])
    await run_next(jenv, api_registry())
    row = jenv.row(job_id)
    assert row.state == "waiting_for_approval"
    return job_id, row.approval_revision_id


def plant_running(jenv: JobsEnv, owner: UUID) -> UUID:
    """A running job of ``owner`` on a class of its own (it counts towards the limit)."""
    _, job_id = jenv.job(
        owner,
        state="running",
        attempt=1,
        leased_by="other-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    return job_id


def owner_lock_key(owner: UUID) -> str:
    return f"{OWNER_LOCK_NAMESPACE}{owner}"
