"""Helpers for the player connection tests (ADR 0034 section 3, #1059): an in-process fake of the
player's source API behind ``PlayerSourceHttp`` (httpx ``MockTransport``, never the network), an
app with the OpenID Connect provider on, browsers that hold a session, and the link dance.
TEST ONLY: public test values, invented users."""

import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import ORIGIN, Env, session_cookie
from classbeat.db import user_session
from classbeat.player_link.link_policy import challenge_of
from classbeat.player_link.source_http import PlayerSourceHttp
from ctx import make_context
from jobtools import engine_for
from mfatools import Actor
from oidctools import expected_subject, oidc_env

PLAYER = "https://player.example"
START = "/api/connections/player/start"
STATUS = "/api/connections/player"
CALLBACK = "/connections/player/callback"


def token(kind: str) -> str:
    return f"cbp_{kind}_" + secrets.token_urlsafe(32)


@dataclass
class Grant:
    redirect_uri: str
    challenge: str
    login_hint: str
    used: bool = False


@dataclass
class FakePlayer:
    """The two calls classbeat makes. ``subject`` overrides what ``account_subject`` says
    (``None`` = the login hint under ``issuer``; ``"omit"`` = no ``account_subject`` at all)."""

    issuer: str
    grants: dict[str, Grant] = field(default_factory=lambda: dict[str, Grant]())
    tokens: list[str] = field(default_factory=lambda: list[str]())
    ended: list[str] = field(default_factory=lambda: list[str]())
    seen: list[httpx.Request] = field(default_factory=lambda: list[httpx.Request]())
    subject: Any = None
    exchange_status: int = 200
    delete_status: int = 204
    down: bool = False

    def approve(self, link_url: str) -> dict[str, str]:
        """The browser at the player's ``/link/source``: the callback's query."""
        parts = urlsplit(link_url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{PLAYER}/link/source"
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert query["source"] == "classbeat"
        assert "code_verifier" not in query
        code = token("lc")
        self.grants[code] = Grant(
            query["redirect_uri"], query["code_challenge"], query["login_hint"]
        )
        return {"code": code, "state": query["state"]}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.down:
            raise httpx.ConnectError("the fake player is down", request=request)
        if request.method == "POST" and request.url.path == "/api/source/token":
            return self._exchange(json.loads(request.content))
        if request.method == "DELETE" and request.url.path == "/api/source/link":
            bearer = request.headers.get("authorization", "")
            self.ended.append(bearer.removeprefix("Bearer "))
            return httpx.Response(self.delete_status)
        return httpx.Response(404)

    def _exchange(self, body: dict[str, str]) -> httpx.Response:
        if self.exchange_status != 200:
            return httpx.Response(self.exchange_status, json={"error": "invalid_grant"})
        grant = self.grants.get(body.get("code", ""))
        if (
            grant is None
            or grant.used
            or body.get("source") != "classbeat"
            or body.get("redirect_uri") != grant.redirect_uri
            or challenge_of(body.get("code_verifier", "")) != grant.challenge
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        grant.used = True
        issued = token("src")
        self.tokens.append(issued)
        answer: dict[str, Any] = {"source_token": issued, "token_type": "Bearer"}
        if self.subject is None:
            answer["account_subject"] = {"iss": self.issuer, "sub": grant.login_hint}
        elif self.subject != "omit":
            answer["account_subject"] = self.subject
        return httpx.Response(200, json=answer)


@dataclass
class Linker:
    env: Env
    fake: FakePlayer
    clients: list[httpx.AsyncClient] = field(default_factory=lambda: list[httpx.AsyncClient]())
    users: dict[int, UUID] = field(default_factory=lambda: dict[int, UUID]())

    @property
    def issuer(self) -> str:
        return self.env.app.state.oidc.issuer

    async def actor(self, email: str) -> Actor:
        return await self.browser(await self.env.user(email))

    async def browser(self, user_id: UUID) -> Actor:
        """Another browser (another session) of ``user_id``."""
        issued = await self.env.login(user_id)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.env.app), base_url=ORIGIN)
        self.clients.append(client)
        actor = Actor(self.env, issued.token, client)
        self.users[id(actor)] = user_id
        return actor

    def user_id(self, actor: Actor) -> UUID:
        return self.users[id(actor)]

    def subject(self, actor: Actor) -> str:
        return expected_subject(self.user_id(actor))

    async def start(self, actor: Actor) -> str:
        response = await actor.post(START)
        assert response.status_code == 200, response.text
        return response.json()["link_url"]

    async def callback(
        self, actor: Actor, query: dict[str, str], *, path: str = CALLBACK
    ) -> dict[str, str]:
        """The callback as the browser follows the redirect; the account page's query."""
        response = await actor.client.get(path, params=query, headers=session_cookie(actor.token))
        actor.client.cookies.clear()
        assert response.status_code == 302, response.text
        assert response.headers["cache-control"] == "no-store"
        location = response.headers["location"]
        assert location.startswith("/account/player?")
        return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}

    async def connect(self, actor: Actor) -> dict[str, str]:
        return await self.callback(actor, self.fake.approve(await self.start(actor)))

    async def owned(self, user_id: UUID, sql: str, **params: Any) -> list[Any]:
        """Rows of ``sql`` as the user (row security applies)."""
        async with user_session(self.env.factory, make_context(user_id)) as db:
            return list((await db.execute(text(sql), params)).all())

    async def connection(self, user_id: UUID) -> Any:
        rows = await self.owned(
            user_id,
            "SELECT id, status, public, secret, wrapped_dek, generation FROM connections "
            "WHERE provider = 'player'",
        )
        return rows[0] if rows else None

    async def flows(self, user_id: UUID) -> list[Any]:
        return await self.owned(
            user_id, "SELECT id, used_at, expires_at FROM player_link_flows ORDER BY created_at"
        )


@pytest.fixture
async def linker(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Linker]:
    async with oidc_env(app_database_url, kek_dir) as env:
        fake = FakePlayer(issuer=env.app.state.oidc.issuer)
        env.app.state.player_source = PlayerSourceHttp(
            PLAYER, lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
        )
        found = Linker(env, fake)
        try:
            yield found
        finally:
            for client in found.clients:
                await client.aclose()


@pytest.fixture
def player_admin(database_url: str) -> Iterator[Connection]:
    """The superuser, autocommit: probes across owners (row security bypassed)."""
    with (
        engine_for(database_url, isolation_level="AUTOCOMMIT") as engine,
        engine.connect() as connection,
    ):
        yield connection
