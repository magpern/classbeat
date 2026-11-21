"""The harness of the studio-track job tests (#1302, #1353): the fake gateway, the fake spotdown
(``spotdownfake``, in process) and ``analyse_tracks`` run as a job, so the real queue, fence and
database apply. Never the network: the autouse netguard refuses any other connection."""

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from authtools import Env, running_env
from classbeat.connections.swedebeat_account import open_bearer
from classbeat.design import mixfetch
from classbeat.design.track_analysis import analyse_tracks
from classbeat.design.track_cache import TrackMap
from classbeat.jobs import Complete, JobKind, JobRun, KindRegistry
from ctx import make_context
from gatewaycore import FakeGateway
from gatewayfake import CLIENT_KEY, store_bearer
from jobtools import new_job
from mediafake import kick_wav_bytes
from optools import KIND as JOB_KIND
from optools import OpsEnv

ONE, TWO = "trk-one-01", "trk-two-02"
AUDIO = kick_wav_bytes(30.0)
OTHER_AUDIO = kick_wav_bytes(31.0)
SHA = hashlib.sha256(AUDIO).hexdigest()
KEY = bytes(range(32))


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url) as running:
        yield running


@pytest.fixture(autouse=True)
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "audio-root"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(mixfetch, "work_root", lambda: root)
    return root


class TrackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    references: list[str]


class Harness:
    def __init__(self, ops: OpsEnv, gateway: FakeGateway) -> None:
        self.ops, self.gateway = ops, gateway
        self.results: list[list[TrackMap]] = []
        ops.registry = self.registry  # type: ignore[method-assign]

    def registry(self) -> KindRegistry:
        async def run(job_run: JobRun) -> Complete:
            given = job_run.input
            assert isinstance(given, TrackInput)
            bearer = await open_bearer(job_run.factory, self.ops.keyring, job_run.ctx)
            key = SecretStr(CLIENT_KEY)
            self.results.append(await analyse_tracks(job_run, bearer, key, given.references))
            return Complete()

        registry = KindRegistry()
        registry.register(JobKind(JOB_KIND, TrackInput, ("tracks",), run))
        return registry

    async def user(self) -> UUID:
        owner = await self.ops.user()
        await store_bearer(self.ops.factory, self.ops.keyring, make_context(owner), self.gateway)
        return owner

    async def run(self, owner: UUID, references: list[str]) -> Any:
        body = json.dumps({"references": references})
        job_id = new_job(self.ops.admin, owner, input=body, max_attempts=1)
        await self.ops.run_next()
        return self.ops.row(job_id)

    def maps(self, owner: UUID) -> list[tuple[str, bytes, Any]]:
        rows = self.ops.sql(
            "SELECT reference, sha256, map FROM swedebeat_track_maps WHERE owner_id = :o "
            "ORDER BY reference",
            o=owner,
        ).all()
        return [(r.reference, bytes(r.sha256), r.map) for r in rows]


@pytest.fixture
def harness(xenv: OpsEnv, gateway_api: FakeGateway) -> Harness:
    gateway_api.add_audio(ONE, AUDIO)
    gateway_api.add_audio(TWO, OTHER_AUDIO)
    return Harness(xenv, gateway_api)


async def baseline(harness: Harness, references: list[str]) -> tuple[list[Any], list[Any]]:
    """The result and stored maps of a run without shadow mode (another owner, the same audio)."""
    owner = await harness.user()
    row = await harness.run(owner, references)
    assert (row.state, row.error_category) == ("completed", None)
    result = [(t.reference, t.sha256, t.music_map, t.cached) for t in harness.results[-1]]
    return result, [(r, s, m) for r, s, m in harness.maps(owner)]


def last_result(harness: Harness) -> list[Any]:
    return [(t.reference, t.sha256, t.music_map, t.cached) for t in harness.results[-1]]
