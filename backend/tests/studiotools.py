"""Helpers for the ``studio_songs`` tests (ADR 0036 sections 2 and 3). TEST ONLY, never the network.

On ``songtools.SongEnv`` (the app, the in-process fake Spotify, ``FakeProvider``) with:

- a studio catalogue of 40 invented tracks, ``Ride Song <i> g<i // 10>`` by ``Made-up Band <i %
  3>``, 240 s on Spotify: a search ``g0`` finds tracks 0 to 9, ``g1`` 10 to 19 and so on;
- the fake SwedeBeat gateway behind the production seam (``gatewayfake.gateway_api``), its
  catalogue set per test with ``found``, its pauses not slept;
- the studio on (``design``), SwedeBeat on, and riders with a Spotify connection (and the pool
  consent), a SwedeBeat connection and the "I use Swedebeat" preference, and **no MOWL
  connection**. The fake MOWL of ``SongEnv`` stays on the network only to show that nothing
  reaches it.

Public test values only.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from authtools import ORIGIN as WEB_ORIGIN
from classbeat import connections, swedebeat_match
from classbeat.connections.spotify_consent_policy import CONSENT_VERSION
from classbeat.jobs import KindRegistry, queue
from classbeat.jobs.runner import Execution, RunConfig, execute
from classbeat.swedebeat_match import config as match_config
from classbeat.swedebeat_match import gateway_match
from classbeat.swedebeat_match.config import MatchConfig
from classbeat.swedebeat_studio import config as studio_config
from classbeat.swedebeat_studio import match as studio_match
from classbeat.swedebeat_studio.kind import STUDIO_SONGS
from ctx import make_context
from gatewaycore import MATCH_PATH, FakeGateway
from gatewayfake import CLIENT_KEY, ORIGIN, calls, store_bearer
from jobapi import Browser
from pickfake import POOL_14, call, entries, turn
from pickfake import songs as song_refs
from preftools import use_swedebeat
from songtools import TRACK_MS, SongEnv, new_key, song_id
from spotifycore import NARROW_ALBUM
from spotifyfake import CLIENT_A, USER_A

KIND = STUDIO_SONGS.name
BRIEF: dict[str, Any] = {"length_min": 30, "intensity": "moderate", "climbs": 1, "sprints": 2}
LIST_8 = [
    ("pool", 0, "warmup"), ("pool", 2, "build"), ("pool", 4, "climb"),
    ("pool", 10, "recovery"), ("pool", 6, "sprint"), ("pool", 7, "sprint"),
    ("pool", 11, "recovery"), ("pool", 12, "cooldown"),
]  # fmt: skip


def title(i: int) -> str:
    return f"Ride Song {i} g{i // 10}"


def artist(i: int) -> str:
    return f"Made-up Band {i % 3}"


def studio_catalogue(size: int = 40) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for i in range(size):
        tid = song_id(i)
        out[tid] = {
            "id": tid,
            "name": title(i),
            "artist": artist(i),
            "artist_id": f"FakeArtist{i % 3:012d}",
            "album": f"Invented Album {i // 10}",
            "album_id": NARROW_ALBUM if i == 2 else f"FakeAlbum{i // 10:013d}",
            "release_date": f"{2000 + i}-01-01",
            "duration_ms": TRACK_MS,
        }
    return out


def pool_turns(groups: tuple[str, ...], picks: list[tuple[int, str]]) -> list[dict[str, Any]]:
    return [
        turn(*[call("search_tracks", {"query": g}, expect={"ok": True}) for g in groups]),
        turn(call("check_pool", entries(*picks), expect={"ok": True, "valid": True})),
        turn(call("submit_pool", entries(*picks), expect={"accepted": True})),
    ]


def choose_turns(listed: list[tuple[str, int, str]]) -> list[dict[str, Any]]:
    return [
        turn(call("get_pool", {}, expect={"ok": True}, contains=['"tracks"'])),
        turn(call("check_song_list", song_refs(*listed), expect={"valid": True})),
        turn(
            call(
                "submit_song_list",
                song_refs(*listed, note="Built up."),
                expect={"accepted": True},
            )
        ),
    ]


def script(*parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"model": "fake-model", "turns": [t for part in parts for t in part]}


def pick_script(listed: list[tuple[str, int, str]] | None = None) -> dict[str, Any]:
    return script(pool_turns(("g0", "g1"), POOL_14), choose_turns(listed or LIST_8))


@dataclass
class StudioRider:
    browser: Browser
    spotify_connection_id: UUID
    swedebeat_connection_id: UUID

    @property
    def user_id(self) -> UUID:
        return self.browser.user_id

    def input(self, **extra: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "brief": BRIEF,
            "spotify_connection_id": str(self.spotify_connection_id),
            "swedebeat_connection_id": str(self.swedebeat_connection_id),
        }
        if "class_id" not in extra:
            values["title"] = "Studio climb"
        return values | extra

    async def create(self, **extra: Any) -> httpx.Response:
        body = {"kind": KIND, "input": self.input(**extra)}
        return await self.browser.post("/api/jobs", body, {"Idempotency-Key": new_key()})

    async def act(
        self,
        class_id: object,
        revision_id: object,
        action: str,
        position: int | None = None,
        *,
        key: str | None = None,
    ) -> httpx.Response:
        body = {} if position is None else {"position": position}
        path = f"/api/classes/{class_id}/studio-songs/{revision_id}/{action}"
        return await self.browser.post(path, body, {"Idempotency-Key": key or new_key()})


@dataclass
class StudioEnv:
    songs: SongEnv
    gateway: FakeGateway
    clients: list[httpx.AsyncClient] = field(default_factory=lambda: list[httpx.AsyncClient]())

    def found(self, *tracks: int, status: str = "found") -> None:
        for i in tracks:
            self.gateway.add_track(title(i), artist(i), status)

    def matches(self) -> list[Any]:
        return calls(self.gateway, MATCH_PATH)

    async def rider(self) -> StudioRider:
        env = self.songs.spot.env
        owner = await env.user(f"studio-{uuid.uuid4().hex[:10]}@example.com")
        self.songs.sql(
            "INSERT INTO entitlements (owner_id, plan_id, features, source) "
            "VALUES (:o, 'instructor', ARRAY['song_picking'], 'grant')",
            o=owner,
        )
        ctx = make_context(owner)
        await self.songs.spot.seed(ctx, CLIENT_A, USER_A)
        assert await connections.give_pool_consent(self.songs.factory, ctx, CONSENT_VERSION)
        await store_bearer(self.songs.factory, self.songs.ring, ctx, self.gateway)
        await use_swedebeat(self.songs.factory, owner)
        issued = await env.login(owner)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=WEB_ORIGIN)
        self.clients.append(client)
        ids = {
            r.provider: r.id
            for r in self.songs.sql(
                "SELECT provider, id FROM connections WHERE owner_id = :o", o=owner
            ).all()
        }
        assert set(ids) == {"spotify", "swedebeat"}  # no MOWL connection at all
        return StudioRider(
            Browser(env, issued.token, client, owner), ids["spotify"], ids["swedebeat"]
        )

    async def created(self, rider: StudioRider, **extra: Any) -> UUID:
        response = await rider.create(**extra)
        assert response.status_code == 201, response.text
        return UUID(response.json()["id"])

    async def run(self, job_id: UUID) -> Any:
        songs = self.songs
        songs.sql("UPDATE jobs SET available_at = now() WHERE id = :id", id=job_id)
        lease = songs.spot.env.settings.job_lease_seconds
        row = await queue.claim(songs.factory, "studio-w", lease, [KIND])
        assert row is not None, "nothing to claim"
        registry = KindRegistry()
        registry.register(STUDIO_SONGS)
        await execute(
            Execution(row),
            songs.factory,
            registry,
            RunConfig(lease_seconds=lease, spotify_calls_per_job=songs.calls_per_job),
            asyncio.Event(),
        )
        return songs.row(job_id)

    async def waiting(self, rider: StudioRider, **extra: Any) -> tuple[UUID, UUID, str]:
        job_id = await self.created(rider, **extra)
        row = await self.run(job_id)
        assert row.state == "waiting_for_approval", (row.state, row.error_category)
        return job_id, row.approval_revision_id, bytes(row.approval_hash).hex()


@pytest.fixture
async def studio(
    songenv: SongEnv, gateway_api: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[StudioEnv]:
    async def no_pause(seconds: float) -> None:
        return None

    monkeypatch.setattr(gateway_match, "_pause", no_pause)
    monkeypatch.setattr(studio_match, "_pause", no_pause)
    songenv.fake.catalogue = studio_catalogue()
    songenv.script = pick_script()
    previous = match_config.current()
    swedebeat_match.configure(
        MatchConfig(
            enabled=True,
            keyring=songenv.ring,
            swedebeat_gateway_url=ORIGIN,
            swedebeat_gateway_key=SecretStr(CLIENT_KEY),
        )
    )
    studio_config.configure(studio_config.StudioConfig(frozenset({"design"})))
    made = StudioEnv(songenv, gateway_api)
    try:
        yield made
    finally:
        studio_config.configure(None)
        swedebeat_match.configure(previous)
        for client in made.clients:
            await client.aclose()
