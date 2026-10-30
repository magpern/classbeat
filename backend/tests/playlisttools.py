"""Helpers for the Spotify write tests (ADR 0008 section 2, issue OP1). TEST ONLY, never the
network: the in-process fake Spotify (``spotifycore``) answers the vendored library through the
worker's real allowlist opener (the ``spot`` fixture), and the token client is the fake's.

``PlayEnv`` runs a ``test_kind`` job whose one stage calls the real ``classbeat.playlists`` steps
(``write_pool``, ``create_final_playlist``, ``fill_final_playlist``) in a registry of its own; a
test may set ``hook`` to act between the job's start (its connection opened) and the write.
Public test values only: invented apps, users and tracks.
"""

import asyncio
import json
import re
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from classbeat import connections, playlists
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.playlists import common
from ctx import make_context
from jobtools import digest, new_job
from spotifycore import API_HOST, FakeSpotify, Seen, track_id
from spotifyfake import CLIENT_A, USER_A
from spotifytools import Spot
from test_classes_schema import insert

KIND = "test_kind"
TRACKS = [track_id(i) for i in range(5)]
MANY = [track_id(i) for i in range(120)]  # a PUT of 100 and a POST of 20; pages of 50 to read


class PlayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    action: Literal["pool", "final_create", "final_fill"] = "pool"
    tracks: list[str] = Field(default_factory=lambda: list(TRACKS))
    revision_id: UUID | None = None
    content_hash: str | None = None
    name: str = "Tuesday climb"
    class_playlist_id: UUID | None = None


@dataclass
class PlayEnv:
    spot: Spot
    admin: Connection
    hook: Callable[[UUID], Awaitable[None]] | None = None
    results: list[Any] = field(default_factory=lambda: list[Any]())

    @property
    def fake(self) -> FakeSpotify:
        return self.spot.fake

    @property
    def factory(self) -> Any:
        return self.spot.env.factory

    @property
    def keyring(self) -> Any:
        return self.spot.ring

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)

    # --- set-up ----------------------------------------------------------------------------

    async def user(self, client_id: str = CLIENT_A, spotify_user: str = USER_A) -> UUID:
        """A classbeat user with a Spotify connection and the pool consent."""
        owner = await self.spot.env.user(f"pick-{uuid.uuid4().hex[:10]}@example.com")
        ctx = make_context(owner)
        await self.spot.seed(ctx, client_id, spotify_user)
        assert await connections.give_pool_consent(self.factory, ctx, 1)
        return owner

    def connection_id(self, owner: UUID) -> UUID:
        return self.sql(
            "SELECT id FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=owner
        ).scalar_one()

    def job(self, owner: UUID, class_id: UUID | None = None, **given: Any) -> UUID:
        body = {"connection_id": str(self.connection_id(owner)), **given}
        return new_job(self.admin, owner, class_id, input=json.dumps(body))

    def song_list(self, owner: UUID, class_id: UUID, name: str = "list") -> tuple[UUID, bytes]:
        """An approved-list stand-in: a ``song_list`` revision of the class."""
        number = self.sql(
            "SELECT coalesce(max(number), 0) + 1 FROM class_revisions WHERE class_id = :c",
            c=class_id,
        ).scalar_one()
        content_hash = digest(f"songs-{name}-{number}")
        revision_id = insert(
            self.admin, "class_revisions", owner_id=owner, class_id=class_id, number=number,
            kind="song_list", content={"v": 1}, content_hash=content_hash, music={},
            provenance={},
        )  # fmt: skip
        return revision_id, content_hash

    def class_of(self, job_id: UUID) -> UUID:
        return self.row(job_id).class_id

    # --- reads -----------------------------------------------------------------------------

    def row(self, job_id: UUID) -> Any:
        return self.sql("SELECT * FROM jobs WHERE id = :id", id=job_id).one()

    def operations(self, owner: UUID, kind: str | None = None) -> list[Any]:
        return self.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o "
            "AND (CAST(:k AS text) IS NULL OR kind = CAST(:k AS text)) ORDER BY created_at",
            o=owner,
            k=kind,
        ).all()

    def pool(self, owner: UUID) -> Any:
        return self.sql("SELECT * FROM spotify_pool WHERE owner_id = :o", o=owner).first()

    def class_playlists(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM class_playlists WHERE owner_id = :o ORDER BY created_at", o=owner
        ).all()

    def events(self, job_id: UUID) -> list[dict[str, Any]]:
        rows = self.sql(
            "SELECT payload FROM job_events WHERE job_id = :id AND kind = 'external_operation' "
            "ORDER BY seq",
            id=job_id,
        ).all()
        return [dict(r.payload) for r in rows]

    def api(self, path: str | None = None, method: str | None = None) -> list[Seen]:
        """The fake's API requests (``path`` a regular expression of the whole path)."""
        seen = self.fake.requests(API_HOST, path)
        return [s for s in seen if method is None or s.method == method]

    def writes(self, playlist_id: str | None = None) -> list[Seen]:
        """Every request that changes a playlist (or creates one)."""
        return [
            s
            for s in self.api()
            if s.method in ("PUT", "POST") and (playlist_id is None or playlist_id in s.path)
        ]

    def entries(self, playlist_id: str) -> list[str | None]:
        return list(self.fake.playlists[playlist_id].entries)

    def ready(self, job_id: UUID) -> None:
        self.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)

    def age(self, operation_id: UUID, seconds: float) -> None:
        self.sql(
            "UPDATE external_operations SET pending_at = pending_at - make_interval(secs => :s), "
            "call_ended_at = call_ended_at - make_interval(secs => :s) WHERE id = :id",
            s=seconds,
            id=operation_id,
        )

    # --- running ---------------------------------------------------------------------------

    def registry(self) -> KindRegistry:
        keyring, env = self.keyring, self

        async def run(job_run: JobRun) -> Complete:
            await job_run.stage("write")
            given = job_run.input
            assert isinstance(given, PlayInput)
            opened = await playlists.open_spotify(job_run, keyring, given.connection_id)
            if env.hook is not None:
                await env.hook(job_run.lease.job_id)
            lease = job_run.lease
            if given.action == "pool":
                env.results.append(
                    await playlists.write_pool(job_run, keyring, opened, given.tracks)
                )
            elif given.action == "final_create":
                assert given.revision_id is not None
                assert given.content_hash is not None
                async with job_run.fenced() as fs:
                    class_id = (
                        await fs.db.execute(
                            text("SELECT class_id FROM jobs WHERE id = :job_id"), lease.params()
                        )
                    ).scalar_one()
                env.results.append(
                    await playlists.create_final_playlist(
                        job_run,
                        keyring,
                        opened,
                        class_id=class_id,
                        revision_id=given.revision_id,
                        content_hash=bytes.fromhex(given.content_hash),
                        name=given.name,
                    )
                )
            else:
                assert given.class_playlist_id is not None
                async with job_run.fenced() as fs:
                    class_id = (
                        await fs.db.execute(
                            text("SELECT class_id FROM jobs WHERE id = :job_id"), lease.params()
                        )
                    ).scalar_one()
                await playlists.fill_final_playlist(
                    job_run,
                    keyring,
                    opened,
                    class_id=class_id,
                    class_playlist_id=given.class_playlist_id,
                    track_ids=given.tracks,
                )
                env.results.append("filled")
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(KIND, PlayInput, ("write",), run))
        return registry

    async def claim(self) -> Execution:
        settings = self.spot.env.settings
        row = await queue.claim(self.factory, "op1-w", settings.job_lease_seconds, [KIND])
        assert row is not None, "nothing to claim"
        return Execution(row)

    async def execute(self, execution: Execution) -> None:
        await execute(
            execution,
            self.factory,
            self.registry(),
            RunConfig(lease_seconds=self.spot.env.settings.job_lease_seconds),
            asyncio.Event(),
        )

    async def run(self, job_id: UUID) -> Any:
        """Run the job's next attempt now; its row afterwards."""
        self.ready(job_id)
        await self.execute(await self.claim())
        return self.row(job_id)

    async def crash_during(self, job_id: UUID, method: str, path: str) -> None:
        """The worker dies while Spotify handles the first ``method`` request to ``path`` (a
        regular expression); Spotify then completes it; the lease ends and the job is reaped."""
        entered, gate = threading.Event(), threading.Event()
        original = self.fake.handle

        def gated(m: str, url: str, headers: dict[str, str], body: bytes | None) -> Any:
            if m == method and not entered.is_set() and re.fullmatch(path, urlsplit(url).path):
                entered.set()
                assert gate.wait(10), "the test never opened the gate"
            return original(m, url, headers, body)

        self.fake.handle = gated  # type: ignore[method-assign]
        self.ready(job_id)
        execution = await self.claim()
        task = asyncio.ensure_future(self.execute(execution))
        assert await asyncio.to_thread(entered.wait, 10), "the request never arrived"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.set()
        self.fake.handle = original  # type: ignore[method-assign]
        self.sql(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id",
            id=job_id,
        )
        assert await queue.reap(self.factory) == 1


@pytest.fixture
async def penv(spot: Spot, spotify_admin: Connection) -> AsyncIterator[PlayEnv]:
    previous = common.runtime()
    common.configure(common.Runtime(http=spot.http))
    try:
        yield PlayEnv(spot, spotify_admin)
    finally:
        common.configure(previous)
