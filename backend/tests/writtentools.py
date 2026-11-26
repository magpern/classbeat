"""A test job kind that runs ``attach_written_playlist`` (#1535, #1546) once per job, on the fake
MOWL attach of ``optools`` (or any fake that serves its ``PUT``). TEST ONLY, never the network.

``Written.job`` queues a ``test_kind`` job naming a Spotify write's key; ``Written.run_next`` runs
the next one with this kind (``OpsEnv``'s own registry runs ``attach_spotify_playlist``). The ids
the kind got back are kept in ``playlists``, in order.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from classbeat.jobs.runner import RunConfig, execute
from classbeat.operations.attach import attach_written_playlist
from jobtools import new_job
from optools import KIND, PLAYLIST, OpsEnv

ATTACH = "mowl_attach_spotify_playlist"


class WrittenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    connection_id: UUID
    write_key: str


@dataclass
class Written:
    ops: OpsEnv
    playlists: list[int] = field(default_factory=lambda: list[int]())

    def job(
        self, owner: UUID, connection_id: UUID, write_key: str, source_id: str = PLAYLIST
    ) -> UUID:
        body = {
            "source_id": source_id,
            "connection_id": str(connection_id),
            "write_key": write_key,
        }
        return new_job(self.ops.admin, owner, input=json.dumps(body))

    def registry(self) -> KindRegistry:
        async def run(job_run: JobRun) -> Complete:
            await job_run.stage("music")
            given = job_run.input
            assert isinstance(given, WrittenInput)
            self.playlists.append(
                await attach_written_playlist(
                    job_run,
                    self.ops.keyring,
                    given.source_id,
                    given.write_key,
                    connection_id=given.connection_id,
                )
            )
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(KIND, WrittenInput, ("music",), run))
        return registry

    async def run(self, job_id: UUID) -> Any:
        """The next queued job (``job_id``) run with this kind; its row afterwards."""
        execution = await self.ops.claim()
        assert execution.claimed.job_id == job_id
        await execute(
            execution,
            self.ops.factory,
            self.registry(),
            RunConfig(lease_seconds=self.ops.app.settings.job_lease_seconds),
            asyncio.Event(),
        )
        return self.ops.row(job_id)

    def attaches(self, owner: UUID) -> list[Any]:
        return self.ops.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o AND kind = :k "
            "ORDER BY created_at",
            o=owner,
            k=ATTACH,
        ).all()

    def link_ids(self, owner: UUID) -> list[UUID]:
        return list(
            self.ops.sql(
                "SELECT id FROM mowl_playlist_links WHERE owner_id = :o", o=owner
            ).scalars()
        )
