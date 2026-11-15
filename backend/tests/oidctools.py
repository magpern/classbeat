"""A fake classbeat-player for the OpenID Connect provider tests (ADR 0034 section 1, #1056).

Everything is local: the provider's ES256 key is generated here, the client secret and pairwise
key are fixed test values written into the test secrets directory (``kek_dir``), and every call
goes through httpx's ASGI transport (netguard holds). Cookies are passed explicitly on each
request and the client's jar is cleared after every answer, so a test says which browser sends
what.
"""

import base64
import hashlib
import json
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import httpx
from joserfc import jwt
from joserfc.jwk import ECKey, KeySet

from authtools import ORIGIN, Env, running_env, session_cookie

CLIENT_ID = "classbeat-player"
REDIRECT_URI = "https://player.example/oidc/callback"
CLIENT_SECRET = "test-player-secret-0123456789abcdefghijklmn"  # 43 characters, a test value
PAIRWISE_KEY = bytes(range(32))  # a fixed, public test key
KID = "test-k1"
OIDC_SETTINGS: dict[str, object] = {
    "oidc_player_client_id": CLIENT_ID,
    "oidc_player_redirect_uri": REDIRECT_URI,
}
FLOW_COOKIE = "__Host-cb_oidc"


def keyset_json(*keys: tuple[str, str, str, datetime]) -> str:
    """A key set file from ``(kid, state, pem, published_at)``."""
    return json.dumps(
        {
            "keys": [
                {
                    "kid": kid,
                    "state": state,
                    "published_at": published.isoformat(),
                    "retired_at": None,
                    "pem": pem,
                }
                for kid, state, pem, published in keys
            ]
        }
    )


def new_pem() -> str:
    return ECKey.generate_key("P-256", private=True).as_pem(private=True).decode("ascii")


def write_provider_files(directory: Path, *, pem: str | None = None) -> str:
    """The three secret files; returns the signing key's PEM."""
    pem = pem or new_pem()
    secret_hash = hashlib.sha256(CLIENT_SECRET.encode()).hexdigest()
    (directory / "oidc_client_secret_hash").write_text(secret_hash + "\n")
    (directory / "oidc_pairwise_key").write_text(PAIRWISE_KEY.hex() + "\n")
    published = datetime.now(UTC) - timedelta(days=2)
    (directory / "oidc_signing_keys").write_text(keyset_json((KID, "current", pem, published)))
    return pem


@asynccontextmanager
async def oidc_env(database_url: str, directory: Path, **overrides: object) -> AsyncIterator[Env]:
    write_provider_files(directory)
    async with running_env(database_url, **OIDC_SETTINGS, **overrides) as env:
        yield env


def basic(client_id: str = CLIENT_ID, secret: str = CLIENT_SECRET) -> dict[str, str]:
    raw = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def query_of(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def flow_cookie_of(response: httpx.Response) -> str | None:
    for header in response.headers.get_list("set-cookie"):
        match = re.match(rf"{FLOW_COOKIE}=([^;]+)", header)
        if match:
            return match.group(1)
    return None


@dataclass
class FakePlayer:
    """One sign-in attempt of the player: its PKCE verifier, state and nonce."""

    env: Env
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    state: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(16))

    def params(self, **overrides: str | None) -> dict[str, str]:
        values: dict[str, str | None] = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid email",
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge": challenge_of(self.verifier),
            "code_challenge_method": "S256",
            **overrides,
        }
        return {k: v for k, v in values.items() if v is not None}

    async def _get(self, url: str, cookies: dict[str, str]) -> httpx.Response:
        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        answer = await self.env.client.get(url, headers={"Cookie": header} if header else {})
        self.env.client.cookies.clear()
        return answer

    async def authorize(self, flow: str | None = None, **overrides: str | None) -> httpx.Response:
        cookies = {FLOW_COOKIE: flow} if flow else {}
        return await self._get(f"/oidc/authorize?{urlencode(self.params(**overrides))}", cookies)

    async def follow(self, location: str, flow: str | None, session: str | None) -> httpx.Response:
        cookies: dict[str, str] = {}
        if flow:
            cookies[FLOW_COOKIE] = flow
        if session:
            cookies.update(_session_pair(session))
        return await self._get(location, cookies)

    async def consent(
        self, request_id: str, flow: str | None, session: str, *, allow: bool = True
    ) -> httpx.Response:
        info = await self.env.client.get("/api/auth/session", headers=session_cookie(session))
        csrf = info.json()["csrf_token"]
        cookie = "; ".join(
            f"{k}={v}" for k, v in {**_session_pair(session), FLOW_COOKIE: flow or ""}.items() if v
        )
        answer = await self.env.client.post(
            "/oidc/authorize/consent",
            json={"r": request_id, "allow": allow},
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf, "Cookie": cookie},
        )
        self.env.client.cookies.clear()
        return answer

    async def token(
        self,
        code: str,
        *,
        verifier: str | None = None,
        redirect_uri: str = REDIRECT_URI,
        headers: dict[str, str] | None = None,
        extra: dict[str, str] | None = None,
    ) -> httpx.Response:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier or self.verifier,
            **(extra or {}),
        }
        answer = await self.env.client.post(
            "/oidc/token", content=urlencode(form), headers={
                "Content-Type": "application/x-www-form-urlencoded", **(headers or basic())
            },
        )  # fmt: skip
        self.env.client.cookies.clear()
        return answer

    async def verify(self, id_token: str) -> dict[str, Any]:
        """The claims, verified against the published key set as the player does it."""
        published = (await self.env.client.get("/oidc/jwks")).json()
        token = jwt.decode(id_token, KeySet.import_key_set(published), algorithms=["ES256"])
        claims = token.claims
        assert token.header["alg"] == "ES256"
        assert token.header["kid"] == KID
        assert token.header["typ"] == "JWT"
        assert claims["iss"] == ORIGIN
        assert claims["aud"] == CLIENT_ID
        assert claims["azp"] == CLIENT_ID
        assert claims["nonce"] == self.nonce
        now = int(datetime.now(UTC).timestamp())
        assert claims["iat"] <= now + 5 < claims["exp"] + 5
        assert claims["exp"] - claims["iat"] == 300
        return claims

    async def code_for(self, session: str) -> str:
        """The whole browser part with a first-time consent; returns the code."""
        started = await self.authorize()
        flow = flow_cookie_of(started)
        assert started.status_code == 302, started.text
        shown = await self.follow(started.headers["location"], flow, session)
        assert shown.status_code == 302, shown.text
        location = shown.headers["location"]
        if location.startswith("/player-consent?"):
            given = await self.consent(query_of(location)["r"], flow, session)
            assert given.status_code == 200, given.text
            location = given.json()["redirect"]
        query = query_of(location)
        assert location.startswith(REDIRECT_URI + "?"), location
        assert query["state"] == self.state
        return query["code"]


def _session_pair(token: str) -> dict[str, str]:
    name, _, value = session_cookie(token)["Cookie"].partition("=")
    return {name: value}


def expected_subject(user_id: UUID) -> str:
    """The pinned rule, computed independently of the provider's code."""
    import hmac

    digest = hmac.new(
        PAIRWISE_KEY, CLIENT_ID.encode() + b"\x00" + user_id.bytes, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
