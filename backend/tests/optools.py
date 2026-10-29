"""Helpers for the external-operation tests (ADR 0005 section 7, X1): a fake MOWL attach and a
running app with the job tables.

``FakeMowlAttach`` answers ``PUT https://api.mowl.com/v1/Spotify/Playlist`` in-process, as the
network handler of the worker's real allowlist opener (``classbeat.egress``), so every attach a
test makes goes through the vendored ``mowl`` library and the allowlist guard and never reaches
the network. Each fake account (its token) has its own playlist ids. Public test values only.
"""

import asyncio
import io
import json
import threading
import urllib.request
import urllib.response
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from email.message import Message
from typing import Any
from urllib.error import URLError
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat import connections
from classbeat.egress import WORKER_ALLOWLIST, build_allowlist_opener
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.operations import attach_spotify_playlist
from ctx import make_context
from jobtools import events, job, new_job

ATTACH_URL = "https://api.mowl.com/v1/Spotify/Playlist"
PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"  # a Spotify id shape; public test value
OTHER_PLAYLIST = "4hOKQuZbraPDIfaGbM3lKI"
TOKEN_A = "aaaaaaaa-0000-1111-2222-333333333333"
TOKEN_B = "bbbbbbbb-0000-1111-2222-333333333333"
ACCOUNT_BASE = {TOKEN_A: 1000, TOKEN_B: 2000}
KIND = "test_kind"


@dataclass
class FakeMowlAttach(urllib.request.BaseHandler):
    """``mode``: ``ok``; ``timeout_after_accept`` (attaches, then the read times out);
    ``refused`` (connection refused: never left); ``status:<n>`` (an HTTP status, nothing
    attached); ``garbled`` (attaches, answers without a ``PlaylistID``)."""

    mode: str = "ok"
    attaches: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    requests: list[str] = field(default_factory=lambda: list[str]())
    gate: threading.Event | None = None  # when set up, each call waits for it
    entered: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)  # an answer went back

    def __hash__(self) -> int:  # urllib keeps handlers in lists and sorts them
        return id(self)

    def https_open(self, req: urllib.request.Request) -> Any:
        try:
            return self._serve(req)
        finally:
            self.finished.set()

    def _serve(self, req: urllib.request.Request) -> Any:
        self.requests.append(f"{req.get_method()} {req.full_url}")
        self.entered.set()
        if self.gate is not None and not self.gate.wait(10):
            raise TimeoutError("test gate never opened")
        if req.full_url != ATTACH_URL or req.get_method() != "PUT":
            return self._answer(req, 404, {"Error": "not found"})
        if self.mode == "refused":
            raise URLError(ConnectionRefusedError(111, "refused"))
        if self.mode.startswith("status:"):
            return self._answer(req, int(self.mode.split(":")[1]), {"Error": "no"})
        token = str(req.get_header("Authorization") or "")
        data = req.data if isinstance(req.data, bytes) else b"{}"
        spotify_id = json.loads(data)["SpotifyPlaylistID"]
        self.attaches.append((token, spotify_id))
        if self.mode == "timeout_after_accept":
            raise TimeoutError("timed out")
        playlist_id = ACCOUNT_BASE.get(token, 9000) + len(self.attaches)
        if self.mode == "garbled":
            return self._answer(req, 200, {"Data": {"Name": "x"}, "Error": None})
        return self._answer(
            req, 200, {"Data": {"PlaylistID": playlist_id, "PlaylistName": "Ride"}, "Error": None}
        )

    def _answer(self, req: urllib.request.Request, status: int, body: Any) -> Any:
        headers = Message()
        headers["Content-Type"] = "application/json"
        response = urllib.response.addinfourl(
            io.BytesIO(json.dumps(body).encode()), headers, req.full_url, status
        )
        response.msg = "fake"  # pyright: ignore[reportAttributeAccessIssue]
        return response


@pytest.fixture
def fake_attach(monkeypatch: pytest.MonkeyPatch) -> FakeMowlAttach:
    fake = FakeMowlAttach()
    opener = build_allowlist_opener(WORKER_ALLOWLIST, https_handler=fake)
    monkeypatch.setattr(urllib.request, "_opener", opener)
    return fake


class AttachInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    connection_id: UUID


@dataclass
class OpsEnv:
    app: Env
    admin: Connection
    playlists: list[int] = field(default_factory=lambda: list[int]())

    @property
    def factory(self) -> Any:
        return self.app.factory

    @property
    def keyring(self) -> Any:
        return self.app.app.state.keyring

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)

    async def user(self) -> UUID:
        return await self.app.user(f"ops-{uuid.uuid4().hex[:10]}@example.com")

    async def connect(self, owner: UUID, token: str = TOKEN_A) -> UUID:
        return await connections.save(
            self.factory, self.keyring, make_context(owner), "mowl", {"token": token}
        )

    def job(self, owner: UUID, connection_id: UUID, source_id: str = PLAYLIST) -> UUID:
        body = {"source_id": source_id, "connection_id": str(connection_id)}
        return new_job(self.admin, owner, input=json.dumps(body))

    def row(self, job_id: UUID) -> Any:
        return job(self.admin, job_id)

    def events(self, job_id: UUID) -> list[tuple[int, str, dict[str, Any], int]]:
        return events(self.admin, job_id)

    def operations(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o ORDER BY created_at", o=owner
        ).all()

    def links(self, owner: UUID) -> list[tuple[str, int]]:
        rows = self.sql(
            "SELECT source_id, playlist_id FROM mowl_playlist_links WHERE owner_id = :o", o=owner
        ).all()
        return [(r.source_id, r.playlist_id) for r in rows]

    def ready(self, job_id: UUID) -> None:
        self.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)

    def registry(self) -> KindRegistry:
        async def run(job_run: JobRun) -> Complete:
            await job_run.stage("music")
            given = job_run.input
            assert isinstance(given, AttachInput)
            self.playlists.append(
                await attach_spotify_playlist(
                    job_run, self.keyring, given.source_id, connection_id=given.connection_id
                )
            )
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(KIND, AttachInput, ("music",), run))
        return registry

    async def claim(self) -> Execution:
        row = await queue.claim(self.factory, "ops-w", self.app.settings.job_lease_seconds, [KIND])
        assert row is not None, "nothing to claim"
        return Execution(row)

    async def execute(self, execution: Execution, shutdown: asyncio.Event | None = None) -> None:
        await execute(
            execution,
            self.factory,
            self.registry(),
            RunConfig(lease_seconds=self.app.settings.job_lease_seconds),
            shutdown or asyncio.Event(),
        )

    async def run_next(self) -> Execution:
        execution = await self.claim()
        await self.execute(execution)
        return execution


@pytest.fixture
def xenv(env: Env, jobs_admin: Connection) -> OpsEnv:
    return OpsEnv(env, jobs_admin)


async def wait_entered(fake: FakeMowlAttach) -> None:
    assert await asyncio.to_thread(fake.entered.wait, 10), "the fake was never called"


async def first_done(*tasks: Awaitable[Any]) -> tuple[set[Any], set[Any]]:
    futures = [asyncio.ensure_future(t) for t in tasks]
    return await asyncio.wait(futures, timeout=15, return_when=asyncio.FIRST_COMPLETED)
