"""A fake classbeat-player events endpoint for the player event tests (ADR 0034 section 2).

``FakePlayer`` answers ``POST /api/classbeat/events`` through ``httpx.MockTransport`` (netguard
holds), verifies each logout token against the provider's published key set as the player does
(ES256, ``typ`` ``logout+jwt``, the ``kid``, issuer, audience, a five-minute lifetime, exactly the
two pinned events, no ``nonce`` or ``sid``) and records the claims. ``statuses`` scripts its
answers; a request after the script ends gets ``200``."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet

from authtools import ORIGIN, Env
from classbeat.egress import player_test_client
from oidctools import CLIENT_ID, KID

BACKCHANNEL = "http://schemas.openid.net/event/backchannel-logout"
GRANT = (
    "INSERT INTO oidc_grants (user_id, client_id, consent_at, first_issued_at) "
    "VALUES (:u, 'classbeat-player', now(), now())"
)


@dataclass
class FakePlayer:
    jwks: dict[str, Any]
    statuses: list[int] = field(default_factory=list)
    received: list[dict[str, Any]] = field(default_factory=list)
    headers: list[httpx.Headers] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/classbeat/events"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert "sec-fetch-site" not in request.headers
        form = parse_qs(request.content.decode("ascii"), strict_parsing=True)
        assert set(form) == {"logout_token"}
        assert len(form["logout_token"]) == 1
        self.received.append(self.verify(form["logout_token"][0]))
        self.headers.append(request.headers)
        return httpx.Response(self.statuses.pop(0) if self.statuses else 200)

    def verify(self, token: str) -> dict[str, Any]:
        keys = KeySet.import_key_set(cast(Any, self.jwks))
        decoded = jwt.decode(token, keys, algorithms=["ES256"])
        header, claims = decoded.header, decoded.claims
        assert header == {"alg": "ES256", "typ": "logout+jwt", "kid": KID}
        assert claims["iss"] == ORIGIN
        assert claims["aud"] == CLIENT_ID
        assert claims["exp"] - claims["iat"] == 300
        assert set(claims) == {"iss", "aud", "iat", "exp", "jti", "sub", "events"}
        assert len(claims["jti"]) == 43
        assert len(claims["sub"]) == 43
        events = claims["events"]
        assert BACKCHANNEL in events
        assert len(events) == 2
        assert all(value == {} for value in events.values())
        return claims

    def client_factory(self) -> Callable[[], httpx.AsyncClient]:
        return lambda: player_test_client(httpx.MockTransport(self.handler))


async def fake_player(env: Env) -> FakePlayer:
    return FakePlayer((await env.client.get("/oidc/jwks")).json())


def event_of(claims: dict[str, Any]) -> str:
    (urn,) = set(claims["events"]) - {BACKCHANNEL}
    return urn
