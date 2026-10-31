"""Helpers for the ``design_with_songs`` tests (ADR 0008, issues SJ1 and SJ2). TEST ONLY, never
the network.

One world per test: the app with song picking on (``spot``), the in-process fake Spotify with an
invented catalogue (``CATALOGUE``: 20 tracks of 240 s, ``alpha`` finds tracks 0 to 9 and ``beta``
10 to 19), and ``FakeMowlPool``, a fake MOWL that wraps a Spotify playlist on attach and lists,
for its MOWL playlist, **what the fake Spotify's playlist holds now** (so a pool write shows up in
MOWL as MOWL is assumed to follow it), unless a test makes it ``stale`` (it keeps listing other
ids). Both answer as the network handler of the worker's real allowlist opener.

``SongEnv`` makes users (a Spotify connection with the pool consent and a MOWL connection), their
browsers (the jobs API with CSRF and an idempotency key), runs the worker side of a job with the
real kind (``DESIGN_WITH_SONGS``) and the ``FakeProvider`` script ``pick_script``, and counts what
reached each fake. Public test values only: invented apps, users, tracks and tokens.
"""

import asyncio
import copy
import json
import re
import secrets
import urllib.request
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from mowl import library
from sqlalchemy import text
from sqlalchemy.engine import Connection

from agentfake import CONFIG, FactoryModels
from authtools import ORIGIN
from classbeat import connections, design, songs
from classbeat.agent.providers.fake import FakeProvider, FakeScript
from classbeat.design import music as design_music
from classbeat.design.kind import Runtime
from classbeat.egress import WORKER_ALLOWLIST, build_allowlist_opener
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.playlists import common
from ctx import make_context
from jobapi import Browser
from jobtools import grant_plan
from optools import ACCOUNT_BASE, ATTACH_URL, TOKEN_A, FakeMowlAttach
from pickfake import LIST_8, POOL_14, call, entries, turn
from pickfake import songs as song_refs
from spotifycore import NARROW_ALBUM, FakeSpotify, Seen
from spotifyfake import CLIENT_A, USER_A, SpotifyHTTPS
from spotifytools import Spot

KIND = songs.KIND
BRIEF: dict[str, Any] = {"length_min": 30, "intensity": "moderate", "climbs": 1, "sprints": 2}
TRACK_MS = 240_000
_PREFIX = "https://api.mowl.com"
_TRACKS = re.compile(r"^/v1/Playlists/(\d+)/tracks$")
_AVAILABILITY = re.compile(r"^/v1/Playlists/(\d+)/sessiondesigner/availability$")
_SECTIONS = re.compile(r"^/v1/Spotify/Tracks/([0-9A-Za-z]+)/AudioAnalysis$")
_URI = re.compile(r"^spotify:track:([0-9A-Za-z]{22})$")


NARROW_SONG = 2  # its album has no image of 64 px or more


def song_id(i: int) -> str:
    return f"SongPick{i:014d}"  # 22 letters and digits


def catalogue(size: int = 20) -> dict[str, dict[str, Any]]:
    """Invented tracks: ``Ride Song <i> alpha|beta`` by ``Made-up Band <i % 3>``, 240 s. Song 2
    (chosen by ``pick_script``) is on ``NARROW_ALBUM``, whose images are all under 64 px, so it
    has no cover; every other song has its album's 64 px cover."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(size):
        tid = song_id(i)
        out[tid] = {
            "id": tid,
            "name": f"Ride Song {i} {'alpha' if i < 10 else 'beta'}",
            "artist": f"Made-up Band {i % 3}",
            "artist_id": f"FakeArtist{i % 3:012d}",
            "album": f"Invented Album {i // 10}",
            "album_id": NARROW_ALBUM if i == NARROW_SONG else f"FakeAlbum{i // 10:013d}",
            "release_date": f"{2000 + i}-01-01",
            "duration_ms": TRACK_MS,
        }
    return out


@dataclass(eq=False)
class FakeMowlPool(FakeMowlAttach):
    """The attach of ``FakeMowlAttach`` plus the reads of ``library.spotify_analysis``, answered
    from the fake Spotify's playlist the MOWL playlist wraps. ``stale``: per Spotify playlist id,
    the ids MOWL keeps listing instead; ``no_bpm``: track ids MOWL never has a BPM for."""

    spotify: FakeSpotify | None = None
    wraps: dict[int, str] = field(default_factory=lambda: dict[int, str]())
    stale: dict[str, list[str]] = field(default_factory=lambda: dict[str, list[str]]())
    no_bpm: set[str] = field(default_factory=lambda: set[str]())
    reads: list[str] = field(default_factory=lambda: list[str]())

    def __hash__(self) -> int:
        return id(self)

    def _serve(self, req: urllib.request.Request) -> Any:
        url = req.full_url
        if (req.get_method(), url) == ("PUT", ATTACH_URL):
            answer = super()._serve(req)
            if answer.status == 200 and self.mode == "ok":
                token = str(req.get_header("Authorization") or "")
                data = req.data if isinstance(req.data, bytes) else b"{}"
                pid = ACCOUNT_BASE.get(token, 9000) + len(self.attaches)
                self.wraps[pid] = json.loads(data)["SpotifyPlaylistID"]
            return answer
        if not url.startswith(_PREFIX):
            return super()._serve(req)
        path = url.removeprefix(_PREFIX).split("?")[0]
        self.requests.append(f"{req.get_method()} {url}")
        self.reads.append(path)
        token = str(req.get_header("Authorization") or "")
        if token not in ACCOUNT_BASE:
            return self._answer(req, 401, {"Error": "unauthorized"})
        return self._read(req, path, token)

    def _ids(self, spotify_playlist: str) -> list[str]:
        if spotify_playlist in self.stale:
            return list(self.stale[spotify_playlist])
        assert self.spotify is not None
        with self.spotify.lock:
            found = self.spotify.playlists.get(spotify_playlist)
            uris = list(found.entries) if found is not None else []
        return [m[1] for u in uris if u and (m := _URI.match(u))]

    def _ok(self, req: urllib.request.Request, data: Any) -> Any:
        return self._answer(req, 200, {"Data": data, "Error": None})

    def _owned(self, token: str, pid: int) -> str | None:
        base = ACCOUNT_BASE[token]
        return self.wraps.get(pid) if base < pid < base + 1000 else None

    def _read(self, req: urllib.request.Request, path: str, token: str) -> Any:
        assert self.spotify is not None
        if path == "/v1/Spotify/Playlists" and req.get_method() == "POST":
            return self._ok(req, [])
        if (m := _TRACKS.match(path)) and (wrapped := self._owned(token, int(m[1]))):
            tracks = []
            for n, tid in enumerate(self._ids(wrapped)):
                t = self.spotify.catalogue.get(tid, {"name": "Gone", "artist": "Gone"})
                tracks.append(
                    {
                        "TrackID": 50_000 + n,
                        "PlaylistID": int(m[1]),
                        "Title": t["name"],
                        "Artist": t["artist"],
                        "Duration": int(t.get("duration_ms", TRACK_MS)),
                        "BPM": 0 if tid in self.no_bpm else 100 + 3 * (int(tid[-3:]) % 30),
                        "SpotifyTrackID": tid,
                        "Url": "x",
                    }
                )
            return self._ok(req, tracks)
        if (m := _AVAILABILITY.match(path)) and self._owned(token, int(m[1])):
            return self._ok(req, {"PlaylistErrors": []})
        if (m := _SECTIONS.match(path)) and m[1] in self.spotify.catalogue:
            sections = [
                {"start": 0.0, "duration": 60.0, "loudness": -12.0},
                {"start": 60.0, "duration": 60.0, "loudness": -6.0},
                {"start": 120.0, "duration": 60.0, "loudness": -11.0},
                {"start": 180.0, "duration": 60.0, "loudness": -5.0},
            ]
            return self._ok(req, {"sections": copy.deepcopy(sections)})
        return self._answer(req, 404, {"Error": "not found"})


class Network(urllib.request.BaseHandler):
    """The worker's network: the fake Spotify's hosts first, then the fake MOWL."""

    def __init__(self, spotify: SpotifyHTTPS, mowl: FakeMowlPool) -> None:
        self.spotify = spotify
        self.mowl = mowl

    def __hash__(self) -> int:
        return id(self)

    def https_open(self, req: urllib.request.Request) -> Any:
        answer = self.spotify.https_open(req)
        return answer if answer is not None else self.mowl.https_open(req)


def pool_turns(*, searches: bool = True) -> list[dict[str, Any]]:
    first = []
    if searches:
        first = [
            turn(
                call("search_tracks", {"query": "alpha"}, expect={"ok": True}),
                call("search_tracks", {"query": "beta"}, expect={"ok": True}),
            )
        ]
    return [
        *first,
        turn(call("check_pool", entries(*POOL_14), expect={"ok": True, "valid": True})),
        turn(call("submit_pool", entries(*POOL_14), expect={"accepted": True})),
    ]


def choose_turns(listed: list[tuple[str, int, str]] | None = None) -> list[dict[str, Any]]:
    chosen = listed or LIST_8
    return [
        turn(call("get_pool", {}, expect={"ok": True}, contains=['"tracks"'])),
        turn(call("check_song_list", song_refs(*chosen), expect={"valid": True})),
        turn(
            call(
                "submit_song_list",
                song_refs(*chosen, note="A steady build to the climb."),
                expect={"accepted": True},
            )
        ),
    ]


def pick_script() -> dict[str, Any]:
    return {"model": "fake-model", "turns": [*pool_turns(), *choose_turns()]}


# The chosen songs of ``pick_script``: pool indexes of LIST_8 are refs, so ids of these tracks.
CHOSEN = [song_id(i) for _, i, _ in LIST_8]
POOL = [song_id(r) for r, _ in POOL_14]


DESIGN_SCRIPT = Path(__file__).parent / "agent_scripts" / "good_first_draft.json"


def design_script() -> dict[str, Any]:
    """Phase 2's model: the ``design`` stage's good first draft (30 minutes on the 8 songs).
    Phase 2's scripts carry ``drafts``; ``SongEnv.waiting`` goes back to ``pick_script`` then."""
    return json.loads(DESIGN_SCRIPT.read_text(encoding="utf-8"))


def failing_script(error: str) -> dict[str, Any]:
    """A model that answers its first turn with ``error`` (``overloaded``: retried; ``auth``:
    ``model_config``, not retried)."""
    return {"drafts": {}, "turns": [{"error": error}]}


def new_key() -> str:
    return secrets.token_urlsafe(18)


@dataclass
class Rider:
    browser: Browser
    spotify_connection_id: UUID
    mowl_connection_id: UUID

    @property
    def user_id(self) -> UUID:
        return self.browser.user_id

    def input(self, **extra: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "brief": BRIEF,
            "mowl_connection_id": str(self.mowl_connection_id),
            "spotify_connection_id": str(self.spotify_connection_id),
        }
        if "class_id" not in extra:
            values["title"] = "Tuesday climb"
        return values | extra

    async def create(self, **extra: Any) -> httpx.Response:
        body = {"kind": KIND, "input": self.input(**extra)}
        return await self.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})

    async def approve(self, job_id: object, revision_id: object, digest: str) -> httpx.Response:
        return await self.browser.approve(job_id, revision_id, digest)

    async def resume(self, class_id: object, revision_id: object) -> httpx.Response:
        resume = {"song_list_revision_id": str(revision_id)}
        return await self.create(class_id=str(class_id), mode="resume", resume=resume)


@dataclass
class SongEnv:
    spot: Spot
    admin: Connection
    mowl: FakeMowlPool
    script: dict[str, Any] = field(default_factory=pick_script)
    providers: list[FakeProvider] = field(default_factory=lambda: list[FakeProvider]())
    clients: list[httpx.AsyncClient] = field(default_factory=lambda: list[httpx.AsyncClient]())
    plan: str | None = None  # granted to each rider (``plantools.users_plan``)

    @property
    def fake(self) -> FakeSpotify:
        return self.spot.fake

    @property
    def factory(self) -> Any:
        return self.spot.env.factory

    @property
    def ring(self) -> Any:
        return self.spot.ring

    def sql(self, statement: str, **params: Any) -> Any:
        return self.admin.execute(text(statement), params)

    def new_provider(self) -> FakeProvider:
        made = FakeProvider(FakeScript.model_validate_json(json.dumps(self.script)))
        self.providers.append(made)
        return made

    # --- set-up ----------------------------------------------------------------------------

    async def rider(
        self,
        *,
        client_id: str = CLIENT_A,
        spotify_user: str = USER_A,
        mowl_token: str = TOKEN_A,
        consent: bool = True,
    ) -> Rider:
        env = self.spot.env
        owner = await env.user(f"songs-{uuid.uuid4().hex[:10]}@example.com")
        if self.plan is not None:  # ``plantools.users_plan``
            grant_plan(self.admin, owner, self.plan)
        # The default ``song_picking`` flag is ``granted`` (ADR 0011 section 7): a rider has the
        # feature, as an admin grants it to the owner's testers.
        self.sql(
            "INSERT INTO entitlements (owner_id, features, source) "
            "VALUES (:o, ARRAY['song_picking'], 'grant')",
            o=owner,
        )
        ctx = make_context(owner)
        await self.spot.seed(ctx, client_id, spotify_user)
        if consent:
            assert await connections.give_pool_consent(self.factory, ctx, 1)
        mowl_id = await connections.save(
            self.factory, self.ring, ctx, "mowl", {"token": mowl_token}
        )
        issued = await env.login(owner)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN)
        self.clients.append(client)
        spotify_id = self.sql(
            "SELECT id FROM connections WHERE owner_id = :o AND provider = 'spotify'", o=owner
        ).scalar_one()
        return Rider(Browser(env, issued.token, client, owner), spotify_id, mowl_id)

    async def created(self, rider: Rider, **extra: Any) -> UUID:
        response = await rider.create(**extra)
        assert response.status_code == 201, response.text
        return UUID(response.json()["id"])

    # --- the worker ------------------------------------------------------------------------

    def registry(self) -> KindRegistry:
        registry = KindRegistry()
        registry.register(songs.DESIGN_WITH_SONGS)
        registry.register(songs.REVISE_SONGS)
        return registry

    async def claim(self) -> Execution | None:
        lease = self.spot.env.settings.job_lease_seconds
        row = await queue.claim(self.factory, "sj1-w", lease, [KIND, songs.REVISE_SONGS.name])
        return None if row is None else Execution(row)

    async def run(self, job_id: UUID) -> Any:
        """The job's next attempt now; its row afterwards."""
        self.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
        execution = await self.claim()
        assert execution is not None, "nothing to claim"
        await execute(
            execution,
            self.factory,
            self.registry(),
            RunConfig(lease_seconds=self.spot.env.settings.job_lease_seconds),
            asyncio.Event(),
        )
        return self.row(job_id)

    async def waiting(self, rider: Rider, **extra: Any) -> tuple[UUID, UUID, str]:
        """A pick of ``rider`` run to its wait: (job id, revision id, content hash hex)."""
        if "drafts" in self.script:
            self.script = pick_script()  # a phase-2 script set by an earlier step
        job_id = await self.created(rider, **extra)
        row = await self.run(job_id)
        assert row.state == "waiting_for_approval", (row.state, row.error_category)
        return job_id, row.approval_revision_id, bytes(row.approval_hash).hex()

    async def approved(self, rider: Rider, **extra: Any) -> tuple[UUID, UUID]:
        """A pick of ``rider`` run to its wait and approved: (job id, revision id); the next
        run's model is ``design_script``."""
        job_id, revision_id, digest = await self.waiting(rider, **extra)
        answer = await rider.approve(job_id, revision_id, digest)
        assert answer.status_code == 200, answer.text
        self.script = design_script()
        return job_id, revision_id

    # --- reads -----------------------------------------------------------------------------

    def row(self, job_id: UUID) -> Any:
        return self.sql("SELECT * FROM jobs WHERE id = :id", id=job_id).one()

    def revision(self, revision_id: UUID) -> Any:
        return self.sql("SELECT * FROM class_revisions WHERE id = :id", id=revision_id).one()

    def kinds(self, job_id: UUID) -> list[str]:
        rows = self.sql(
            "SELECT kind FROM job_events WHERE job_id = :id ORDER BY seq", id=job_id
        ).all()
        return [r.kind for r in rows]

    def stages(self, job_id: UUID) -> list[str]:
        rows = self.sql(
            "SELECT payload ->> 'stage' AS stage FROM job_events WHERE job_id = :id "
            "AND kind = 'stage_started' ORDER BY seq",
            id=job_id,
        ).all()
        return [r.stage for r in rows]

    def pool(self, owner: UUID) -> Any:
        return self.sql("SELECT * FROM spotify_pool WHERE owner_id = :o", o=owner).first()

    def operations(self, owner: UUID, kind: str | None = None) -> list[Any]:
        return self.sql(
            "SELECT * FROM external_operations WHERE owner_id = :o "
            "AND (CAST(:k AS text) IS NULL OR kind = CAST(:k AS text)) ORDER BY created_at",
            o=owner,
            k=kind,
        ).all()

    def class_playlists(self, owner: UUID) -> list[Any]:
        return self.sql(
            "SELECT * FROM class_playlists WHERE owner_id = :o ORDER BY created_at", o=owner
        ).all()

    def creates(self) -> int:
        """Playlist creates the fake Spotify saw (the pool's and every final one)."""
        return sum(1 for s in self.api() if (s.method, s.path) == ("POST", "/v1/me/playlists"))

    def writes(self) -> int:
        """Every Spotify write the fake saw (creates and item writes)."""
        return sum(1 for s in self.api() if s.method in ("POST", "PUT", "DELETE"))

    def api(self) -> list[Seen]:
        return self.fake.requests("api.spotify.com")

    def counts(self) -> tuple[int, int, int]:
        """Requests seen so far by the fake Spotify, the fake MOWL, and model turns sent."""
        turns = sum(len(p.sent) for p in self.providers)
        return len(self.fake.requests()), len(self.mowl.requests), turns


@pytest.fixture
async def songenv(
    spot: Spot,
    spotify_admin: Connection,
    monkeypatch: pytest.MonkeyPatch,
    users_plan: str | None,
) -> AsyncIterator[SongEnv]:
    spot.fake.catalogue = catalogue()
    mowl = FakeMowlPool(spotify=spot.fake)
    network = Network(spot.library, mowl)
    monkeypatch.setattr(
        urllib.request, "_opener", build_allowlist_opener(WORKER_ALLOWLIST, https_handler=network)
    )
    monkeypatch.setattr(library, "BPM_POLL_SEC", 0.01)
    monkeypatch.setattr(library, "BPM_WAIT_SEC", 0.2)
    monkeypatch.setattr(design_music, "SYNC_POLL_SECONDS", 0.01)
    made = SongEnv(spot, spotify_admin, mowl, plan=users_plan)
    previous = common.runtime()
    common.configure(common.Runtime(http=spot.http))
    design.configure(
        Runtime(keyring=spot.ring, models=FactoryModels(made.new_provider), agent=CONFIG)
    )
    songs.configure(songs.SongConfig(enabled=True, sync_wait_seconds=0.3))
    try:
        yield made
    finally:
        songs.configure(None)
        design.configure(None)
        common.configure(previous)
        for client in made.clients:
            await client.aclose()


Mutate = Callable[[dict[str, Any]], None]
