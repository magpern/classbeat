"""Helpers for the ``save_workout`` tests (SV1): a fake MOWL that also keeps programs, and a
running app with a class, a stored draft revision and its music link.

``FakeMowlPrograms`` extends the music fake of ``designtools``: in-process, as the network
handler of the worker's real allowlist opener, it answers what ``mowl.workout.save`` writes
(``PUT v1/programs``, ``PUT v1/segments``, ``DELETE v1/segments/<id>/intervals``,
``PUT v1/segments/<id>/intervals/multiple``, ``PUT v1/programs/<id>/segments``) and the two reads
(``POST v1/programs``, My Workouts newest first, and ``GET v1/programs/<id>``) per account (its
token). **Failure injection per write request number** (``fail``; 1 is the program, then three
per segment, then the segment list): ``refuse`` (never applied, connection refused), ``drop``
(applied, then the answer is lost), ``noid`` (applied, answered without its id). ``hold_at``
stops a write request before it is applied until ``release`` is set. Public test values only;
never the network.
"""

import copy
import json
import re
import threading
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest
from mowl import workout

from authtools import ORIGIN, Env
from classbeat import saving
from classbeat.agent.schemas import WorkoutDraft
from classbeat.design.revision import canonical
from classbeat.egress import WORKER_ALLOWLIST, build_allowlist_opener
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from designtools import FakeMowlMusic
from jobapi import Browser, new_key
from jobtools import new_job
from optools import ACCOUNT_BASE, PLAYLIST, TOKEN_A, OpsEnv

_PREFIX = "https://api.mowl.com"
_PROGRAM = re.compile(r"^/v1/programs/(\d+)$")
_PROGRAM_SEGMENTS = re.compile(r"^/v1/programs/(\d+)/segments$")
_INTERVALS = re.compile(r"^/v1/segments/(\d+)/intervals$")
_INTERVALS_MULTIPLE = re.compile(r"^/v1/segments/(\d+)/intervals/multiple$")
POSITION_NAMES = {1: "Seated", 2: "Standing"}
CYCLE_NAMES = {1: "SB", 4: "ACC"}
FIRST_PROGRAM = 600_000
FIRST_SEGMENT = 900_000

DRAFT: dict[str, Any] = WorkoutDraft.model_validate(
    {
        "name": "Tuesday climb",
        "description": "Invented test class",
        "segments": [
            {
                "name": "Warm up",
                "kind": "warmup",
                "intervals": [
                    {"sec": 300, "zone": 2, "rpm": [85, 95], "standing": False, "cue": "easy"}
                ],
            },
            {
                "name": "The climb",
                "kind": "main",
                "intervals": [
                    {"sec": 240, "zone": 4, "rpm": [60, 70], "standing": True},
                    {"sec": 60, "zone": 2, "rpm": [90, 100], "standing": False},
                    {
                        "sec": 120,
                        "zone": 5,
                        "rpm": [95, 110],
                        "standing": False,
                        "game": "racetrack",
                    },
                ],
            },
            {
                "name": "Cool down",
                "kind": "cooldown",
                "intervals": [{"sec": 180, "zone": 1, "rpm": [80, 90], "standing": False}],
            },
        ],
    }
).model_dump(mode="json", exclude_none=True)
SEGMENTS = len(DRAFT["segments"])
WRITES = 2 + 3 * SEGMENTS  # the requests ``workout.save`` makes for DRAFT


def draft_hash(content: dict[str, Any]) -> bytes:
    import hashlib

    return hashlib.sha256(canonical(content)).digest()


def spec_of(content: dict[str, Any]) -> dict[str, Any]:
    return WorkoutDraft.model_validate(content).to_spec()


@dataclass
class Program:
    id: int
    token: str
    body: dict[str, Any]
    segment_ids: list[int] = field(default_factory=lambda: list[int]())
    public: bool = False


@dataclass
class Segment:
    id: int
    token: str
    body: dict[str, Any]
    intervals: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())


@dataclass(eq=False)
class FakeMowlPrograms(FakeMowlMusic):
    """See the module docstring. ``list_status``/``get_status``: an HTTP status for every
    listing or program read (200 when None)."""

    programs: dict[int, Program] = field(default_factory=lambda: dict[int, Program]())
    segments: dict[int, Segment] = field(default_factory=lambda: dict[int, Segment]())
    fail: dict[int, str] = field(default_factory=lambda: dict[int, str]())
    write_count: int = 0
    writes: list[str] = field(default_factory=lambda: list[str]())  # "<n> <METHOD> <path>"
    write_tokens: list[str] = field(default_factory=lambda: list[str]())
    lists: int = 0
    gets: int = 0
    list_status: int | None = None
    get_status: int | None = None
    hold_at: int | None = None
    held: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    _next_program: int = FIRST_PROGRAM
    _next_segment: int = FIRST_SEGMENT
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __hash__(self) -> int:
        return id(self)

    # --- planting programs the user made in the MOWL app ----------------------------------

    def plant(
        self,
        token: str,
        spec: dict[str, Any] | None = None,
        *,
        playlist_id: int = -1,
        public: bool = False,
        name: str | None = None,
    ) -> int:
        """A complete program of ``token``'s, newest so far (``spec`` or a one-segment one)."""
        spec = copy.deepcopy(spec) if spec is not None else _other_spec(name or "Made in the app")
        with self._lock:
            program = self._new_program(token, _program_body(spec, playlist_id))
            program.public = public
            for s in spec["segments"]:
                segment = self._new_segment(token, _segment_body(s))
                segment.intervals = [workout.interval_body(i) for i in s["intervals"]]
                program.segment_ids.append(segment.id)
        return program.id

    def owned(self, token: str) -> list[Program]:
        return [p for p in self.programs.values() if p.token == token]

    def _new_program(self, token: str, body: dict[str, Any]) -> Program:
        self._next_program += 1
        program = Program(self._next_program, token, body)
        self.programs[program.id] = program
        return program

    def _new_segment(self, token: str, body: dict[str, Any]) -> Segment:
        self._next_segment += 1
        segment = Segment(self._next_segment, token, body)
        self.segments[segment.id] = segment
        return segment

    # --- serving --------------------------------------------------------------------------

    def _serve(self, req: urllib.request.Request) -> Any:
        url = req.full_url
        path = url.removeprefix(_PREFIX).split("?")[0] if url.startswith(_PREFIX) else ""
        method = req.get_method()
        if path == "/v1/programs" and method == "POST":
            return self._listing(req)
        if (m := _PROGRAM.match(path)) and method == "GET":
            return self._program(req, int(m[1]))
        if method in ("PUT", "DELETE") and (
            path in ("/v1/programs", "/v1/segments")
            or _PROGRAM_SEGMENTS.match(path)
            or _INTERVALS.match(path)
            or _INTERVALS_MULTIPLE.match(path)
        ):
            return self._write(req, method, path)
        return super()._serve(req)

    def _token(self, req: urllib.request.Request) -> str:
        return str(req.get_header("Authorization") or "")

    def _ok(self, req: urllib.request.Request, data: Any) -> Any:
        return self._answer(req, 200, {"Data": data, "Error": None})

    def _listing(self, req: urllib.request.Request) -> Any:
        self.requests.append(f"POST {req.full_url}")
        self.lists += 1
        token = self._token(req)
        if token not in ACCOUNT_BASE:
            return self._answer(req, 401, {"Error": "unauthorized"})
        if self.list_status is not None:
            return self._answer(req, self.list_status, {"Error": "no"})
        form = {k: v[0] for k, v in parse_qs(bytes(req.data or b"").decode()).items()}  # pyright: ignore[reportArgumentType]
        page, per_page = int(form.get("PageNumber", 0)), int(form.get("itemsPerPage", 50))
        newest = sorted(self.owned(token), key=lambda p: p.id, reverse=True)
        shown = newest[page * per_page : (page + 1) * per_page]
        return self._ok(
            req,
            [{"ProgramID": p.id, "Name": p.body["Name"], "IsPublic": p.public} for p in shown],
        )

    def _program(self, req: urllib.request.Request, program_id: int) -> Any:
        self.requests.append(f"GET {req.full_url}")
        self.gets += 1
        token = self._token(req)
        program = self.programs.get(program_id)
        if self.get_status is not None:
            return self._answer(req, self.get_status, {"Error": "no"})
        if program is None or program.token != token:
            return self._answer(req, 200, {"Data": None, "Error": "No program found"})
        return self._ok(req, self.read_program(program))

    def read_program(self, program: Program) -> dict[str, Any]:
        """``GET v1/programs/<id>`` as the library's ``summarize`` expects it."""
        segments = []
        total = 0
        for sid in program.segment_ids:
            s = self.segments[sid]
            intervals = []
            for body in s.intervals:
                shown = copy.deepcopy(body)
                shown["PositionTypeName"] = POSITION_NAMES[body["PositionTypeID"]]
                shown["CycleName"] = CYCLE_NAMES[body["CycleID"]]
                if body.get("FtpTest"):
                    shown["FtpTest"] = {**body["FtpTest"], "DisplayName": "FTP test"}
                intervals.append(shown)
            total += int(s.body["TotalDuration"])
            segments.append({"SegmentID": sid, **s.body, "Intervals": intervals})
        body = program.body
        return {
            "ProgramID": program.id,
            "Name": body["Name"],
            "Description": body.get("Description"),
            "ShortDescription": body.get("ShortDescription"),
            "IsPublic": program.public,
            "PlaylistID": body.get("PlaylistID"),
            "Playlist": {"PlaylistType": "Spotify", "PlaylistName": "Invented"},
            "TotalDuration": total,
            "TSS": body.get("TSS"),
            "Segments": segments,
        }

    def _write(self, req: urllib.request.Request, method: str, path: str) -> Any:
        with self._lock:
            self.write_count += 1
            number = self.write_count
        self.requests.append(f"{method} {req.full_url}")
        self.writes.append(f"{number} {method} {path}")
        token = self._token(req)
        self.write_tokens.append(token)
        if self.hold_at == number:
            self.held.set()
            if not self.release.wait(10):
                raise TimeoutError("test release never came")
        if token not in ACCOUNT_BASE:
            return self._answer(req, 401, {"Error": "unauthorized"})
        mode = self.fail.get(number, "ok")
        if mode == "refuse":
            raise URLError(ConnectionRefusedError(111, "refused"))
        body: Any = json.loads(req.data) if isinstance(req.data, bytes) and req.data else None
        with self._lock:
            data = self._apply(token, method, path, body)
        if mode == "drop":
            raise TimeoutError("timed out")
        if mode == "noid" and isinstance(data, dict):
            data = {k: v for k, v in data.items() if not k.endswith("ID")}  # pyright: ignore[reportUnknownVariableType]
        if data is _MISSING:
            return self._answer(req, 200, {"Data": None, "Error": "not found"})
        return self._ok(req, data)

    def _apply(self, token: str, method: str, path: str, body: Any) -> Any:
        if path == "/v1/programs":
            program = self._new_program(token, dict(body))
            return {"ProgramID": program.id, "Name": program.body["Name"]}
        if path == "/v1/segments":
            segment = self._new_segment(token, dict(body))
            return {"SegmentID": segment.id}
        if m := _PROGRAM_SEGMENTS.match(path):
            program = self.programs.get(int(m[1]))
            if program is None or program.token != token:
                return _MISSING
            program.segment_ids = [int(i) for i in body["SegmentIDs"]]
            return {"ProgramID": program.id}
        if m := _INTERVALS_MULTIPLE.match(path):
            segment = self.segments.get(int(m[1]))
            if segment is None or segment.token != token:
                return _MISSING
            segment.intervals = list(body["Intervals"])
            return {"Count": len(segment.intervals)}
        if m := _INTERVALS.match(path):
            segment = self.segments.get(int(m[1]))
            if segment is None or segment.token != token or method != "DELETE":
                return _MISSING
            segment.intervals = []
            return {}
        return _MISSING


_MISSING: Any = object()


def _program_body(spec: dict[str, Any], playlist_id: int) -> dict[str, Any]:
    return {
        "ProgramCategoryID": 17,
        "Name": spec["name"],
        "Description": spec.get("description", ""),
        "ShortDescription": "",
        "PlaylistID": playlist_id,
        "IsPublic": False,
        "TSS": workout.tss(spec),
        "ActivityTypeID": 0,
    }


def _segment_body(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "Name": s["name"],
        "Description": "",
        "ShortDescription": "",
        "IsWarmup": s["kind"] == "warmup",
        "IsActiveRecovery": s["kind"] == "recovery",
        "IsCooldown": s["kind"] == "cooldown",
        "TotalDuration": workout.seconds(s),
        "IsPublic": False,
        "IsTemplate": False,
        "ActivityTypeID": 0,
    }


def _other_spec(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "segments": [
            {
                "name": "Only",
                "kind": "cooldown",
                "intervals": [{"sec": 120, "zone": 1, "rpm": [80, 90]}],
            }
        ],
    }


@pytest.fixture
def fake_programs(monkeypatch: pytest.MonkeyPatch) -> FakeMowlPrograms:
    fake = FakeMowlPrograms()
    opener = build_allowlist_opener(WORKER_ALLOWLIST, https_handler=fake)
    monkeypatch.setattr(urllib.request, "_opener", opener)
    return fake


LINKED_PLAYLIST = ACCOUNT_BASE[TOKEN_A] + 1  # what the fake attach of account A hands out first


@dataclass
class Saved:
    """One user's planted class: the class, its draft revision and the hex hash."""

    owner: UUID
    connection: UUID
    class_id: UUID
    revision_id: UUID
    link_id: UUID
    content: dict[str, Any]

    @property
    def hash_hex(self) -> str:
        return draft_hash(self.content).hex()

    def input(self, **overrides: Any) -> dict[str, Any]:
        return {
            "class_id": str(self.class_id),
            "revision_id": str(self.revision_id),
            "content_hash": self.hash_hex,
            "mowl_connection_id": str(self.connection),
            **overrides,
        }


@dataclass
class SaveEnv:
    ops: OpsEnv
    fake: FakeMowlPrograms

    def sql(self, statement: str, **params: Any) -> Any:
        return self.ops.sql(statement, **params)

    async def owner(self, token: str = TOKEN_A) -> tuple[UUID, UUID]:
        owner = await self.ops.user()
        return owner, await self.ops.connect(owner, token)

    def plant_link(self, owner: UUID, playlist_id: int = LINKED_PLAYLIST) -> UUID:
        return self.sql(
            "INSERT INTO mowl_playlist_links (id, owner_id, source, source_id, playlist_id) "
            "VALUES (:id, :o, 'spotify', :s, :p) ON CONFLICT (owner_id, source, source_id) "
            "DO UPDATE SET playlist_id = mowl_playlist_links.playlist_id RETURNING id",
            id=uuid.uuid4(), o=owner, s=PLAYLIST, p=playlist_id,
        ).scalar_one()  # fmt: skip

    def plant_revision(
        self,
        owner: UUID,
        class_id: UUID,
        link_id: UUID,
        content: dict[str, Any] | None = None,
        *,
        kind: str = "draft",
        content_hash: bytes | None = None,
    ) -> UUID:
        content = copy.deepcopy(DRAFT if content is None else content)
        music = {
            "source": "mowl_spotify_playlist",
            "link_id": str(link_id),
            "fingerprint": "a" * 64,
            "unknown_bpm": [],
        }
        return self.sql(
            "INSERT INTO class_revisions (id, owner_id, class_id, number, kind, content, "
            "content_hash, music, provenance) SELECT :id, :o, :c, coalesce(max(number), 0) + 1, "
            ":k, CAST(:content AS jsonb), :h, CAST(:music AS jsonb), '{}'::jsonb "
            "FROM class_revisions WHERE owner_id = :o AND class_id = :c RETURNING id",
            id=uuid.uuid4(), o=owner, c=class_id, k=kind, content=json.dumps(content),
            h=content_hash or draft_hash(content), music=json.dumps(music),
        ).scalar_one()  # fmt: skip

    async def saved_class(self, token: str = TOKEN_A) -> Saved:
        owner, connection = await self.owner(token)
        return self.plant(owner, connection)

    def plant(self, owner: UUID, connection: UUID, content: dict[str, Any] | None = None) -> Saved:
        class_id = self.sql(
            "INSERT INTO classes (id, owner_id, title) VALUES (:id, :o, 'Tuesday climb') "
            "RETURNING id",
            id=uuid.uuid4(), o=owner,
        ).scalar_one()  # fmt: skip
        link_id = self.plant_link(owner)
        content = copy.deepcopy(DRAFT if content is None else content)
        revision_id = self.plant_revision(owner, class_id, link_id, content)
        return Saved(owner, connection, class_id, revision_id, link_id, content)

    def job(self, saved: Saved, columns: dict[str, Any] | None = None, **overrides: Any) -> UUID:
        """A queued save job as the jobs API stores it (no ``admit``: the worker's checks);
        ``overrides`` change the input, ``columns`` the job row."""
        return new_job(
            self.ops.admin,
            saved.owner,
            saved.class_id,
            kind=saving.KIND,
            input=json.dumps(saved.input(**overrides)),
            credit_state="none",
            **(columns or {}),
        )

    def registry(self) -> KindRegistry:
        registry = KindRegistry()
        registry.register(saving.SAVE_WORKOUT)
        return registry

    async def claim(self) -> Execution:
        lease = self.ops.app.settings.job_lease_seconds
        row = await queue.claim(self.ops.factory, "sv1-w", lease, [saving.KIND])
        assert row is not None, "nothing to claim"
        return Execution(row)

    async def execute(self, execution: Execution, shutdown: Any = None) -> None:
        import asyncio

        await execute(
            execution,
            self.ops.factory,
            self.registry(),
            RunConfig(lease_seconds=self.ops.app.settings.job_lease_seconds),
            shutdown or asyncio.Event(),
        )

    async def run_next(self) -> Execution:
        execution = await self.claim()
        await self.execute(execution)
        return execution

    def row(self, job_id: UUID) -> Any:
        return self.ops.row(job_id)

    def operations(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o AND kind = :k "
            "ORDER BY created_at",
            o=owner, k="mowl_save_workout",
        ).all()  # fmt: skip

    def saved_rows(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM saved_workouts WHERE owner_id = :o ORDER BY created_at", o=owner
        ).all()

    def op_states(self, job_id: UUID) -> list[str]:
        return [
            p["state"] for _, kind, p, _ in self.ops.events(job_id) if kind == "external_operation"
        ]

    def ready(self, job_id: UUID) -> None:
        self.ops.ready(job_id)

    def lose_lease(self, job_id: UUID) -> None:
        self.sql(
            "UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id",
            id=job_id,
        )

    def age(self, operation_id: UUID, seconds: float) -> None:
        self.sql(
            "UPDATE external_operations SET pending_at = pending_at - make_interval(secs => :s), "
            "call_ended_at = call_ended_at - make_interval(secs => :s) WHERE id = :id",
            s=seconds, id=operation_id,
        )  # fmt: skip


@pytest.fixture
def senv(xenv: OpsEnv, fake_programs: FakeMowlPrograms) -> Iterator[SaveEnv]:
    saving.configure(saving.Runtime(xenv.keyring))
    saving.configure_listing_lag(None)
    try:
        yield SaveEnv(xenv, fake_programs)
    finally:
        saving.configure(None)
        saving.configure_listing_lag(None)


@dataclass
class SaveBrowser:
    """A signed-in browser of a planted class's owner, through the real jobs API."""

    browser: Browser
    saved: Saved

    async def save(self, key: str | None = None, **overrides: Any) -> httpx.Response:
        body = {"kind": saving.KIND, "input": self.saved.input(**overrides)}
        return await self.browser.post("/api/jobs", body, {"Idempotency-Key": key or new_key()})


@pytest.fixture
async def save_browser(env: Env, senv: SaveEnv) -> AsyncIterator[Any]:
    clients: list[httpx.AsyncClient] = []

    async def make(saved: Saved) -> SaveBrowser:
        issued = await env.login(saved.owner)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN)
        clients.append(client)
        browser = Browser(env, issued.token, client, saved.owner, saved.connection)
        return SaveBrowser(browser, saved)

    yield make
    for client in clients:
        await client.aclose()
