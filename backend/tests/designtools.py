"""Helpers for the ``design_draft`` tests (D1): a fake MOWL that also serves playlists, tracks,
availability and sections, and a running app with the job tables and the kind's runtime.

``FakeMowlMusic`` extends the attach fake of ``optools``: it answers, in-process and as the
network handler of the worker's real allowlist opener, the reads ``library.spotify_analysis``
makes (``GET v1/Playlists/<id>/tracks``, ``.../sessiondesigner/availability``,
``v1/Spotify/Tracks/<id>/AudioAnalysis``) and ``POST v1/Spotify/Playlists`` (the user's playlists),
so the analysis goes through the vendored library and the allowlist guard and never the network.
A fake account (its token) lists only its own playlists (``names``) and reads only the MOWL
playlists its own attaches handed out. The music is
invented (``agentfake.synthetic_tracks``). Public test values only.
"""

import copy
import json
import re
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.engine import Connection

from agentfake import CONFIG, FactoryModels, FakeLibrary, provider, synthetic_tracks
from authtools import Env
from classbeat import design
from classbeat.agent.providers.fake import FakeProvider
from classbeat.design import DESIGN_DRAFT, Runtime
from classbeat.egress import WORKER_ALLOWLIST, build_allowlist_opener
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from jobtools import new_job
from optools import ACCOUNT_BASE, PLAYLIST, FakeMowlAttach, OpsEnv

PLAYLIST_NAME = "Invented Tuesday Mix"
_TRACKS = re.compile(r"^/v1/Playlists/(\d+)/tracks$")
_AVAILABILITY = re.compile(r"^/v1/Playlists/(\d+)/sessiondesigner/availability$")
_SECTIONS = re.compile(r"^/v1/Spotify/Tracks/([0-9A-Za-z]+)/AudioAnalysis$")
_PREFIX = "https://api.mowl.com"


def owns(token: str, playlist_id: int) -> bool:
    base = ACCOUNT_BASE.get(token, 9000)
    return base < playlist_id < base + 1000


@dataclass(eq=False)
class FakeMowlMusic(FakeMowlAttach):
    """``bpm_zero_reads``: the first N track reads report BPM 0 for every track;
    ``bpm_never``: indexes whose BPM stays 0; ``on_read``: called with each read's path (in the
    library's thread) before it is answered."""

    music: FakeLibrary = field(default_factory=synthetic_tracks)
    # Per account (token): its Spotify playlists as MOWL lists them, id -> name.
    names: dict[str, dict[str, str]] = field(
        default_factory=lambda: {t: {PLAYLIST: PLAYLIST_NAME} for t in ACCOUNT_BASE}
    )
    bpm_zero_reads: int = 0
    bpm_never: set[int] = field(default_factory=lambda: set[int]())
    on_read: Callable[[str], None] | None = None
    reads: list[str] = field(default_factory=lambda: list[str]())
    tokens_read: list[str] = field(default_factory=lambda: list[str]())
    track_reads: int = 0

    def __hash__(self) -> int:
        return id(self)

    def _serve(self, req: urllib.request.Request) -> Any:
        url = req.full_url
        attach = (req.get_method(), url) == ("PUT", _PREFIX + "/v1/Spotify/Playlist")
        if attach or not url.startswith(_PREFIX):
            return super()._serve(req)
        path = url.removeprefix(_PREFIX).split("?")[0]
        self.requests.append(f"{req.get_method()} {url}")
        self.reads.append(path)
        token = str(req.get_header("Authorization") or "")
        self.tokens_read.append(token)
        if self.on_read is not None:
            self.on_read(path)
        if token not in ACCOUNT_BASE:
            return self._answer(req, 401, {"Error": "unauthorized"})
        return self._read(req, path, token)

    def _ok(self, req: urllib.request.Request, data: Any) -> Any:
        return self._answer(req, 200, {"Data": data, "Error": None})

    def _read(self, req: urllib.request.Request, path: str, token: str) -> Any:
        if path == "/v1/Spotify/Playlists" and req.get_method() == "POST":
            listed = [
                {"ID": sid, "Name": name, "TrackCount": len(self.music.tracks), "Url": "x"}
                for sid, name in self.names.get(token, {}).items()
            ]
            return self._ok(req, listed)
        if (m := _TRACKS.match(path)) and owns(token, int(m[1])):
            self.track_reads += 1
            tracks = copy.deepcopy(self.music.tracks)
            for i, t in enumerate(tracks):
                if self.track_reads <= self.bpm_zero_reads or i in self.bpm_never:
                    t["BPM"] = 0
            return self._ok(req, tracks)
        if (m := _AVAILABILITY.match(path)) and owns(token, int(m[1])):
            return self._ok(req, {"PlaylistErrors": copy.deepcopy(self.music.problems)})
        if (m := _SECTIONS.match(path)) and m[1] in self.music.sections:
            return self._ok(req, {"sections": copy.deepcopy(self.music.sections[m[1]])})
        return self._answer(req, 404, {"Error": "not found"})


@pytest.fixture
def fake_mowl(monkeypatch: pytest.MonkeyPatch) -> FakeMowlMusic:
    fake = FakeMowlMusic()
    opener = build_allowlist_opener(WORKER_ALLOWLIST, https_handler=fake)
    monkeypatch.setattr(urllib.request, "_opener", opener)
    return fake


BRIEF: dict[str, Any] = {"length_min": 30, "intensity": "moderate", "climbs": 1, "sprints": 2}


@dataclass
class DesignEnv:
    ops: OpsEnv
    script: str = "good_first_draft"
    providers: list[FakeProvider] = field(default_factory=lambda: list[FakeProvider]())

    @property
    def app(self) -> Env:
        return self.ops.app

    @property
    def admin(self) -> Connection:
        return self.ops.admin

    def new_provider(self) -> FakeProvider:
        made = provider(self.script)
        self.providers.append(made)
        return made

    def runtime(self) -> Runtime:
        return Runtime(
            keyring=self.ops.keyring, models=FactoryModels(self.new_provider), agent=CONFIG
        )

    def input(self, connection_id: UUID, source_id: str = PLAYLIST, **extra: Any) -> dict[str, Any]:
        return {
            "title": "Tuesday climb",
            "brief": BRIEF,
            "music": {"source": "mowl_spotify_playlist", "spotify_playlist_id": source_id},
            "mowl_connection_id": str(connection_id),
            **extra,
        }

    def job(self, owner: UUID, connection_id: UUID, source_id: str = PLAYLIST) -> UUID:
        body = self.input(connection_id, source_id)  # as the jobs API stores it
        return new_job(self.admin, owner, kind=design.KIND, input=json.dumps(body))

    def registry(self) -> KindRegistry:
        registry = KindRegistry()
        registry.register(DESIGN_DRAFT)
        return registry

    async def claim(self) -> Execution:
        settings = self.app.settings
        row = await queue.claim(self.ops.factory, "d1-w", settings.job_lease_seconds, [design.KIND])
        assert row is not None, "nothing to claim"
        return Execution(row)

    async def execute(self, execution: Execution, shutdown: Any = None) -> None:
        import asyncio

        await execute(
            execution,
            self.ops.factory,
            self.registry(),
            RunConfig(lease_seconds=self.app.settings.job_lease_seconds),
            shutdown or asyncio.Event(),
        )

    async def run_next(self) -> Execution:
        execution = await self.claim()
        await self.execute(execution)
        return execution

    def revisions(self, job_id: UUID) -> list[Any]:
        return self.ops.sql(
            "SELECT * FROM class_revisions WHERE job_id = :id ORDER BY number", id=job_id
        ).all()

    def agent_runs(self, job_id: UUID) -> list[Any]:
        return self.ops.sql(
            "SELECT * FROM agent_runs WHERE job_id = :id ORDER BY started_at", id=job_id
        ).all()

    def kinds(self, job_id: UUID) -> list[str]:
        return [kind for _, kind, _, _ in self.ops.events(job_id)]


@pytest.fixture
def denv(xenv: OpsEnv) -> Iterator[DesignEnv]:
    made = DesignEnv(xenv)
    design.configure(made.runtime())
    try:
        yield made
    finally:
        design.configure(None)
