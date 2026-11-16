"""Shared helpers of the SR C7 tests (``produce_outputs``, ADR 0036 section 6): a designed studio
class, the output choice through its consent route, the job run until it is no longer queued, the
user's output view and actions, and a stand-in for the sandboxed render child whose bytes follow
the plan (deterministic, so a changed plan or ``vary`` gives another sha256). Fakes only: the
SwedeBeat gateway (``gatewaycore``), Spotify and MOWL; never the network.
"""

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.studio import render, render_policy
from classbeat.swedebeat_studio import config as studio_config
from classbeat.swedebeat_studio.outputs_kind import PRODUCE_OUTPUTS
from gatewaycore import FIXTURE_MP3, FakeGateway
from songtools import new_key
from studiotools import StudioEnv, StudioRider

ALL_ON = frozenset({"design", "swedebeat_playlist", "swedebeat_mix", "mowl"})
WRITE_PATHS = ("/playlists",)


@pytest.fixture
def outputs_on(studio: StudioEnv) -> Iterator[None]:
    studio_config.configure(studio_config.StudioConfig(ALL_ON))
    yield
    studio_config.configure(studio_config.StudioConfig(frozenset({"design"})))


class Renders:
    """The render child's stand-in: what it was asked to render, and a switch for other bytes."""

    def __init__(self) -> None:
        self.plans: list[list[dict[str, Any]]] = []
        self.vary = False
        self.fail = False

    async def render_files(self, work: Path, tracks: Any, **_: Any) -> render.Rendered:
        plan = [dict(t) for t in tracks]
        self.plans.append(plan)
        for track in plan:  # the tracks are in the render's private directory
            assert Path(track["path"]).parent == work
            assert Path(track["path"]).is_file()  # noqa: ASYNC240 - a test stand-in
        if self.fail:
            raise render.RenderFailed("length")
        checked = render_policy.validate_plan({"output": str(work / "mix.mp3"), "tracks": plan})
        frames = render_policy.composed_frames(checked)
        shape = [[t[k] for k in render_policy.TRACK_INTS] for t in checked]
        varied = len(self.plans) if self.vary else 0
        data = FIXTURE_MP3 + json.dumps({"plan": shape, "vary": varied}).encode()
        out = work / render.OUTPUT_NAME
        out.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return render.Rendered(out, digest, frames, frames / render_policy.RATE, None)


@pytest.fixture(name="renders")
def renders_fixture(monkeypatch: pytest.MonkeyPatch) -> Renders:
    made = Renders()
    monkeypatch.setattr(render, "render_files", made.render_files)
    return made


def choice(current: Any, studio: StudioEnv, *outputs: str) -> dict[str, Any]:
    stored = studio.songs.revision(current.current_revision_id)
    return {
        "revision_id": str(current.current_revision_id),
        "content_hash": bytes(stored.content_hash).hex(),
        "outputs": list(outputs),
    }


async def choose(rider: StudioRider, class_id: UUID, body: dict[str, Any]) -> UUID:
    path = f"/api/classes/{class_id}/outputs"
    answer = await rider.browser.post(path, body, {"Idempotency-Key": new_key()})
    assert answer.status_code == 201, answer.text
    return UUID(answer.json()["job_id"])


async def run_job(studio: StudioEnv, job_id: UUID, *, rounds: int = 6) -> Any:
    """Claim and run the job until it is no longer queued (a retry, a requeue for a slot)."""
    songs = studio.songs
    lease = songs.spot.env.settings.job_lease_seconds
    registry = KindRegistry()
    registry.register(PRODUCE_OUTPUTS)
    for _ in range(rounds):
        songs.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
        row = await queue.claim(songs.factory, "outputs-w", lease, list(registry.names()))
        assert row is not None, "nothing to claim"
        await execute(
            Execution(row),
            songs.factory,
            registry,
            RunConfig(lease_seconds=lease, spotify_calls_per_job=songs.calls_per_job),
            asyncio.Event(),
        )
        if songs.row(job_id).state != "queued":
            break
    return songs.row(job_id)


async def produce(
    studio: StudioEnv, rider: StudioRider, class_id: UUID, current: Any, *outputs: str
) -> Any:
    """Approve ``outputs`` for the current draft and run the job to its end."""
    job_id = await choose(rider, class_id, choice(current, studio, *outputs))
    return await run_job(studio, job_id)


def sent(fake: FakeGateway, method: str, path: str) -> list[Any]:
    """The requests the fake saw: ``path`` exact, or a publish for ``"publish"``."""
    if path == "publish":
        return [r for r in fake.requests if r.method == method and r.path.endswith("/publish")]
    return [r for r in fake.requests if r.method == method and r.path == path]


def rows(studio: StudioEnv, owner: UUID) -> list[Any]:
    return studio.songs.sql(
        "SELECT output, attempt, state, external_id, detail FROM class_outputs "
        "WHERE owner_id = :o ORDER BY output, attempt",
        o=owner,
    ).all()


def operations(studio: StudioEnv, owner: UUID) -> list[Any]:
    return studio.songs.sql(
        "SELECT kind, idempotency_key, state, error_category FROM external_operations "
        "WHERE owner_id = :o ORDER BY created_at",
        o=owner,
    ).all()


async def view(rider: StudioRider, class_id: UUID) -> dict[str, dict[str, Any]]:
    answer = await rider.browser.get(f"/api/classes/{class_id}/outputs")
    assert answer.status_code == 200, answer.text
    return {o["output"]: o for o in answer.json()["outputs"]}


async def act(
    rider: StudioRider, class_id: UUID, output: str, action: str, body: dict[str, Any]
) -> httpx.Response:
    path = f"/api/classes/{class_id}/outputs/{output}/{action}"
    return await rider.browser.post(path, body, {"Idempotency-Key": new_key()})
