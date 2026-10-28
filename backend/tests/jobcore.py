"""Helpers for the jobs-core tests (ADR 0005 sections 2 to 5): a migrated database with the queue
functions, the app role's async engine, the test-only kind and its revision writes.

Setup rows are written as the superuser (``jobtools``); everything under test runs as
``classbeat_app`` through ``classbeat.jobs``.
"""

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from authtools import make_settings
from classbeat.config import Settings
from classbeat.context import UserContext
from classbeat.db import create_engine, create_session_factory
from classbeat.jobs import FencedSession, JobKind, JobRun, KindRegistry, Outcome, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from jobtools import engine_for, events, job, new_job, new_user

KIND = "test_kind"


class KindInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = "fine"


@dataclass
class JobsEnv:
    settings: Settings
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    admin: Connection

    def user(self) -> UUID:
        return new_user(self.admin)

    def job(self, owner: UUID | None = None, **columns: Any) -> tuple[UUID, UUID]:
        owner = owner or self.user()
        return owner, new_job(self.admin, owner, **columns)

    def row(self, job_id: UUID) -> Any:
        return job(self.admin, job_id)

    def events(self, job_id: UUID) -> list[tuple[int, str, dict[str, Any], int]]:
        return events(self.admin, job_id)

    def kinds(self, job_id: UUID) -> list[str]:
        return [kind for _, kind, _, _ in self.events(job_id)]

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)

    def expire_lease(self, job_id: UUID) -> None:
        self.sql(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id",
            id=job_id,
        )

    def counts(self, job_id: UUID) -> tuple[int, int, int]:
        """(events, revisions, agent_runs) of the job."""
        return tuple(  # pyright: ignore[reportReturnType]
            self.sql(f"SELECT count(*) FROM {t} WHERE job_id = :id", id=job_id).scalar_one()  # noqa: S608
            for t in ("job_events", "class_revisions", "agent_runs")
        )


@pytest.fixture
def jobs_admin(clean_auth: None, database_url: str) -> Iterator[Connection]:
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection


@pytest.fixture
async def jenv(jobs_admin: Connection, app_database_url: str) -> AsyncIterator[JobsEnv]:
    settings = make_settings(app_database_url, job_lease_seconds=60)
    engine = create_engine(settings, pool_size=10)
    try:
        yield JobsEnv(settings, engine, create_session_factory(engine), jobs_admin)
    finally:
        await engine.dispose()


def content_hash(content: dict[str, Any]) -> bytes:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).digest()


_INSERT_REVISION = text(
    "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, content_hash, "
    "music, provenance, job_id) "
    "SELECT :id, j.owner_id, j.class_id, coalesce((SELECT max(r.number) FROM class_revisions r "
    "WHERE r.owner_id = j.owner_id AND r.class_id = j.class_id), 0) + 1, :kind, "
    "CAST(:content AS jsonb), :hash, '{}'::jsonb, '{}'::jsonb, j.id "
    "FROM jobs j WHERE j.id = :job_id AND j.owner_id = :owner_id"
)


async def store_revision(fs: FencedSession, kind: str, content: dict[str, Any]) -> UUID:
    revision_id = uuid.uuid4()
    await fs.db.execute(
        _INSERT_REVISION,
        {
            "id": revision_id,
            "kind": kind,
            "content": json.dumps(content, sort_keys=True),
            "hash": content_hash(content),
            "job_id": fs.lease.job_id,
            "owner_id": fs.lease.owner_id,
        },
    )
    await fs.event("revision_created", {"revision_id": revision_id, "kind": kind})
    return revision_id


SONG_LIST = {"songs": [{"track": 0}]}
DRAFT = {"name": "Tuesday climb"}


async def store_list(fs: FencedSession) -> tuple[UUID, bytes]:
    return await store_revision(fs, "song_list", SONG_LIST), content_hash(SONG_LIST)


async def store_draft(fs: FencedSession) -> UUID | None:
    return await store_revision(fs, "draft", DRAFT)


def registry_for(run: Callable[[JobRun], Awaitable[Outcome]]) -> KindRegistry:
    registry = KindRegistry()
    registry.register(JobKind(KIND, KindInput, ("pick", "design"), run))
    return registry


@dataclass
class Upstream:
    """A fake provider: counts the calls a run makes (no network)."""

    calls: int = 0

    async def call(self) -> str:
        self.calls += 1
        await asyncio.sleep(0)
        return f"ext-{self.calls}"


async def claim(env: JobsEnv, worker: str = "test-w") -> Execution:
    row = await queue.claim(env.factory, worker, env.settings.job_lease_seconds, [KIND])
    assert row is not None, "nothing to claim"
    return Execution(row)


async def run_next(
    env: JobsEnv,
    registry: KindRegistry,
    *,
    shutdown: asyncio.Event | None = None,
    config: RunConfig | None = None,
) -> Execution:
    """Claim one test job and run it to the end of its attempt."""
    execution = await claim(env)
    await execute(
        execution,
        env.factory,
        registry,
        config or RunConfig(lease_seconds=env.settings.job_lease_seconds),
        shutdown or asyncio.Event(),
    )
    return execution


class CountingLedger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, uuid.UUID]] = []

    async def reserve(self, db: AsyncSession, ctx: UserContext, job_id: uuid.UUID) -> None:
        self.calls.append(("reserve", job_id))

    async def allows(self, db: AsyncSession, ctx: UserContext, job_id: uuid.UUID) -> bool:
        return True

    async def commit(self, db: AsyncSession, ctx: UserContext, job_id: uuid.UUID) -> None:
        self.calls.append(("commit", job_id))

    async def release(self, db: AsyncSession, ctx: UserContext, job_id: uuid.UUID) -> None:
        self.calls.append(("release", job_id))
