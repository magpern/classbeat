"""The audition's test tools (#1394): the preview volume, a listening session and a clip, and
the ``preview_render`` job run as the worker runs it. Fakes only."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from authtools import session_cookie
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.preview import storage
from classbeat.preview.render_kind import PREVIEW_RENDER
from studiotools import StudioEnv, StudioRider


@pytest.fixture(name="volume")
def volume_fixture(studio: StudioEnv, tmp_path: Path) -> Iterator[Path]:
    """The preview volume, after the app's own start configured it from its settings (unset)."""
    root = tmp_path / "preview"
    root.mkdir(mode=0o700)
    storage.configure(root)
    yield root
    storage.configure(None)


async def run_preview(studio: StudioEnv, job_id: UUID) -> Any:
    songs = studio.songs
    lease = songs.spot.env.settings.job_lease_seconds
    registry = KindRegistry()
    registry.register(PREVIEW_RENDER)
    for _ in range(4):
        songs.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
        row = await queue.claim(songs.factory, "preview-w", lease, list(registry.names()))
        assert row is not None, "nothing to claim"
        config = RunConfig(lease_seconds=lease, spotify_calls_per_job=songs.calls_per_job)
        await execute(Execution(row), songs.factory, registry, config, asyncio.Event())
        if songs.row(job_id).state != "queued":
            break
    return songs.row(job_id)


async def new_session(rider: StudioRider, class_id: object) -> str:
    made = await rider.browser.post(f"/api/classes/{class_id}/preview/sessions", {})
    assert made.status_code == 201, made.text
    return str(made.json()["session_id"])


async def get_clip(
    rider: StudioRider, class_id: object, session: str, start_ms: int = 0, **headers: str
) -> httpx.Response:
    path = f"/api/classes/{class_id}/preview/clip?session={session}&start_ms={start_ms}"
    browser = rider.browser
    answer = await browser.client.get(path, headers=session_cookie(browser.token) | headers)
    browser.client.cookies.clear()
    return answer


async def rendered(
    studio: StudioEnv, rider: StudioRider, class_id: object, session: str, start_ms: int = 0
) -> httpx.Response:
    """Ask for a clip, run its job, and ask again: the served clip."""
    first = await get_clip(rider, class_id, session, start_ms)
    assert first.status_code == 202, first.text
    assert first.headers["cache-control"] == "no-store"
    row = await run_preview(studio, UUID(first.json()["job_id"]))
    assert (row.state, row.error_category, row.class_id) == ("completed", None, None)
    served = await get_clip(rider, class_id, session, start_ms)
    assert served.status_code == 200, served.text
    return served
