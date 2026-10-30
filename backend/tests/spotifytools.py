"""Helpers for the Spotify connection tests (ADR 0009, issue SC1): an app with song picking on,
the in-process fake Spotify behind both of classbeat's paths (``SpotifyHttp`` and the worker's
urllib opener the library uses), browsers that hold a session, and the connect dance. TEST ONLY,
never the network: public test values, invented apps and users."""

import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import ORIGIN, Env, running_env, session_cookie
from classbeat import connections
from classbeat.db import user_session
from classbeat.egress import WORKER_ALLOWLIST, build_allowlist_opener
from classbeat.spotify.http import SpotifyHttp
from ctx import make_context
from jobtools import engine_for
from mfatools import Actor
from spotifycore import ACCOUNTS_HOST, FakeSpotify, Seen
from spotifyfake import CLIENT_A, CLIENT_B, USER_A, USER_B, SpotifyHTTPS, spotify_client

CALLBACK_URL = f"{ORIGIN}/oauth/spotify/callback"
CLIENT_C = "c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2"
START = "/api/connections/spotify/start"
STATUS = "/api/connections/spotify"
CONSENT = "/api/connections/spotify/pool-consent"


class RecordingHTTPS(SpotifyHTTPS):
    """The worker's urllib network handler, recording each path the library sent."""

    def __init__(self, fake: FakeSpotify) -> None:
        super().__init__(fake)
        self.paths: list[str] = []

    def https_open(self, req: urllib.request.Request) -> Any:
        self.paths.append(urlsplit(req.full_url).path)
        return super().https_open(req)


@dataclass
class Spot:
    env: Env
    fake: FakeSpotify
    http: SpotifyHttp
    library: RecordingHTTPS
    clients: list[httpx.AsyncClient] = field(default_factory=lambda: list[httpx.AsyncClient]())
    users: dict[int, UUID] = field(default_factory=lambda: dict[int, UUID]())

    @property
    def ring(self) -> Any:
        return self.env.app.state.keyring

    async def actor(self, email: str, *, base_url: str = ORIGIN) -> Actor:
        user_id = await self.env.user(email)
        return await self.browser(user_id, base_url=base_url)

    async def browser(self, user_id: UUID, *, base_url: str = ORIGIN) -> Actor:
        """Another browser (another session) of ``user_id``."""
        issued = await self.env.login(user_id)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.env.app), base_url=base_url
        )
        self.clients.append(client)
        actor = Actor(self.env, issued.token, client)
        self.users[id(actor)] = user_id
        return actor

    def user_id(self, actor: Actor) -> UUID:
        return self.users[id(actor)]

    def ctx(self, actor: Actor) -> Any:
        return make_context(self.user_id(actor))

    async def start(self, actor: Actor, client_id: str = CLIENT_A, *, confirm: bool = False) -> str:
        response = await actor.post(
            START, {"client_id": client_id, "confirm_account_change": confirm}
        )
        assert response.status_code == 200, response.text
        return response.json()["authorize_url"]

    def approve(self, authorize_url: str, user: str | None = None) -> dict[str, str]:
        """The browser's visit to Spotify's authorize page: the callback's query."""
        if user is not None:
            self.fake.sign_in(user)
        answer = self.fake.handle("GET", authorize_url, {}, None)
        assert answer.status == 302, answer.body
        location = answer.headers["Location"]
        assert location.startswith(CALLBACK_URL + "?")
        return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}

    async def callback(self, actor: Actor, query: dict[str, str]) -> dict[str, str]:
        """The callback, as the browser follows the redirect; the account page's query."""
        response = await actor.client.get(
            "/oauth/spotify/callback", params=query, headers=session_cookie(actor.token)
        )
        actor.client.cookies.clear()
        assert response.status_code == 302, response.text
        location = response.headers["location"]
        assert location.startswith("/account/spotify?")
        return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}

    async def connect(
        self, actor: Actor, client_id: str = CLIENT_A, user: str = USER_A, *, confirm: bool = False
    ) -> dict[str, str]:
        url = await self.start(actor, client_id, confirm=confirm)
        return await self.callback(actor, self.approve(url, user))

    def token_requests(self) -> list[Seen]:
        return self.fake.requests(ACCOUNTS_HOST, "/api/token")

    async def owned(self, ctx: Any, sql: str, **params: Any) -> Any:
        """Rows of ``sql`` as the user (row security: the system session sees none)."""
        async with user_session(self.env.factory, ctx) as db:
            return (await db.execute(text(sql), params)).all()

    async def connection(self, ctx: Any) -> Any:
        rows = await self.owned(
            ctx,
            "SELECT id, status, last_error, public, secret, wrapped_dek, generation, key_id "
            "FROM connections WHERE provider = 'spotify'",
        )
        return rows[0] if rows else None

    async def flows(self, ctx: Any) -> list[Any]:
        return await self.owned(
            ctx, "SELECT id, used_at, expires_at FROM spotify_oauth_flows ORDER BY created_at"
        )

    async def seed(
        self, ctx: Any, client_id: str = CLIENT_A, user: str = USER_A, *, expires_in: float = 3000
    ) -> tuple[str, str]:
        """A connection as if ``user`` had connected ``client_id``: real fake tokens, stored
        with ``expires_at`` ``expires_in`` seconds away."""
        access, refresh = self.fake.grant(client_id, user)
        await connections.save(
            self.env.factory,
            self.ring,
            ctx,
            "spotify",
            {
                "access_token": access,
                "refresh_token": refresh,
                "expires_at": int(time.time() + expires_in),
            },
            public={"client_id": client_id, "spotify_user_id": user},
            confirm_account_change=True,
        )
        return access, refresh


def make_fake() -> FakeSpotify:
    """Three apps on the test origin's callback: A allows user A, B allows user B, C allows
    both (for account changes)."""
    fake = FakeSpotify()
    fake.add_user(USER_A, "Rider One")
    fake.add_user(USER_B, "Rider Two")
    fake.add_app(CLIENT_A, redirect_uris=[CALLBACK_URL], allowed=[USER_A, USER_B])
    fake.add_app(CLIENT_B, redirect_uris=[CALLBACK_URL], allowed=[USER_A, USER_B])
    fake.add_app(CLIENT_C, redirect_uris=[CALLBACK_URL], allowed=[USER_A])
    fake.sign_in(USER_A)
    return fake


@pytest.fixture
async def spot(
    clean_auth: None, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Spot]:
    fake = make_fake()
    library = RecordingHTTPS(fake)
    monkeypatch.setattr(
        urllib.request, "_opener", build_allowlist_opener(WORKER_ALLOWLIST, https_handler=library)
    )
    async with running_env(app_database_url, song_picking=True) as env:
        http = SpotifyHttp(lambda total: spotify_client(fake, total_seconds=total))
        env.app.state.spotify_http = http
        found = Spot(env, fake, http, library)
        try:
            yield found
        finally:
            for client in found.clients:
                await client.aclose()


@pytest.fixture
def spotify_admin(database_url: str) -> Iterator[Connection]:
    """The superuser, autocommit: setup and probes across owners (row security bypassed)."""
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection
