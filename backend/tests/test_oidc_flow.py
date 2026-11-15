"""The OpenID Connect provider's happy path with a fake player (ADR 0034 section 1, #1056)."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

from authtools import ORIGIN, Env
from classbeat.auth.oidc.subject_calc import pairwise_subject
from oidctools import (
    CLIENT_ID,
    FLOW_COOKIE,
    REDIRECT_URI,
    FakePlayer,
    expected_subject,
    flow_cookie_of,
    oidc_env,
    query_of,
)


@pytest.fixture
async def oenv(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    async with oidc_env(app_database_url, kek_dir) as running:
        yield running


def test_the_subject_is_the_pinned_hmac_of_a_test_vector() -> None:
    user = UUID("00000000-0000-4000-8000-000000000001")
    # HMAC-SHA256(bytes 0..31, "player" || 0x00 || the uuid's 16 bytes), base64url, no padding.
    assert pairwise_subject(bytes(range(32)), "player", user) == (
        "PUEyVdkVehNeIqeD5rleYGciFcsvcrvKFpvcZ1bCXVo"
    )
    assert len(pairwise_subject(bytes(range(32)), "player", user)) == 43
    assert pairwise_subject(bytes(range(32)), "other", user) != pairwise_subject(
        bytes(range(32)), "player", user
    )


async def test_the_discovery_document_is_exactly_the_pinned_one(oenv: Env) -> None:
    answer = await oenv.client.get("/.well-known/openid-configuration")
    assert answer.status_code == 200
    assert answer.json() == {
        "issuer": ORIGIN,
        "authorization_endpoint": f"{ORIGIN}/oidc/authorize",
        "token_endpoint": f"{ORIGIN}/oidc/token",
        "jwks_uri": f"{ORIGIN}/oidc/jwks",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": ["ES256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "email"],
        "claims_supported": [
            "iss", "aud", "azp", "sub", "iat", "exp", "auth_time", "nonce", "email",
            "email_verified",
        ],
        "backchannel_logout_supported": True,
        "backchannel_logout_session_supported": False,
    }  # fmt: skip


async def test_the_key_set_publishes_only_public_parts(oenv: Env) -> None:
    keys = (await oenv.client.get("/oidc/jwks")).json()["keys"]
    assert len(keys) == 1
    assert keys[0]["kty"] == "EC"
    assert keys[0]["crv"] == "P-256"
    assert keys[0]["alg"] == "ES256"
    assert keys[0]["use"] == "sig"
    assert "d" not in keys[0]


async def test_a_fake_player_completes_the_code_flow_with_pkce(oenv: Env) -> None:
    user_id = await oenv.user("rider@example.com")
    session = await oenv.login(user_id)
    player = FakePlayer(oenv)
    code = await player.code_for(session.token)
    answer = await player.token(code)
    assert answer.status_code == 200, answer.text
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["pragma"] == "no-cache"
    body = answer.json()
    assert set(body) == {"id_token", "token_type", "expires_in", "access_token"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    claims = await player.verify(body["id_token"])
    assert set(claims) == {
        "iss", "aud", "azp", "sub", "iat", "exp", "auth_time", "nonce", "email", "email_verified"
    }  # fmt: skip
    assert claims["sub"] == expected_subject(user_id)
    assert claims["email"] == "rider@example.com"
    assert claims["email_verified"] is True
    assert isinstance(claims["auth_time"], int)
    assert claims["auth_time"] <= claims["iat"]
    kinds = await oenv.scalar(
        "SELECT array_agg(kind ORDER BY at, id) FROM auth_events WHERE user_id = :u "
        "AND kind LIKE 'oidc_%'",
        u=user_id,
    )
    assert kinds == ["oidc_consent", "oidc_signed_in"]
    grant = await oenv.row(
        "SELECT client_id, consent_at, first_issued_at FROM oidc_grants WHERE user_id = :u",
        u=user_id,
    )
    assert grant.client_id == CLIENT_ID
    assert grant.consent_at
    assert grant.first_issued_at
    # The request and its binding are gone; the code is used.
    assert await oenv.scalar("SELECT count(*) FROM oidc_requests") == 0
    assert await oenv.scalar("SELECT count(*) FROM oidc_codes WHERE used_at IS NULL") == 0


async def test_a_second_sign_in_after_consent_skips_the_consent_page(oenv: Env) -> None:
    user_id = await oenv.user()
    session = await oenv.login(user_id)
    await FakePlayer(oenv).code_for(session.token)
    player = FakePlayer(oenv)
    started = await player.authorize()
    shown = await player.follow(started.headers["location"], flow_cookie_of(started), session.token)
    assert shown.status_code == 302
    assert shown.headers["location"].startswith(REDIRECT_URI + "?code=")
    assert (await player.token(query_of(shown.headers["location"])["code"])).status_code == 200


async def test_the_request_is_kept_while_the_consent_page_is_shown(oenv: Env) -> None:
    """#1056 N14: only the consent POST takes the request (issuing the code or the denial)."""
    user_id = await oenv.user()
    session = await oenv.login(user_id)
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    shown = await player.follow(started.headers["location"], flow, session.token)
    assert shown.headers["location"].startswith("/player-consent?r=")
    assert await oenv.scalar("SELECT count(*) FROM oidc_requests") == 1
    again = await player.follow(started.headers["location"], flow, session.token)
    assert again.headers["location"] == shown.headers["location"]  # a reload still works
    denied = await player.consent(query_of(shown.headers["location"])["r"], flow, session.token,
                                  allow=False)  # fmt: skip
    assert denied.status_code == 200
    assert query_of(denied.json()["redirect"]) == {"error": "access_denied", "state": player.state}
    assert await oenv.scalar("SELECT count(*) FROM oidc_requests") == 0
    assert await oenv.scalar("SELECT count(*) FROM oidc_grants") == 0


async def test_a_reused_flow_cookie_gets_its_max_age_renewed(oenv: Env) -> None:
    """#1056 N16: a new authorize request with an existing cookie sets it again, Max-Age 600."""
    player = FakePlayer(oenv)
    first = await player.authorize()
    flow = flow_cookie_of(first)
    assert flow is not None
    second = await FakePlayer(oenv).authorize(flow=flow)
    headers = [h for h in second.headers.get_list("set-cookie") if h.startswith(FLOW_COOKIE)]
    assert len(headers) == 1
    assert headers[0].startswith(f"{FLOW_COOKIE}={flow};")
    for part in ("Max-Age=600", "Path=/", "Secure", "HttpOnly", "SameSite=lax"):
        assert part.lower() in headers[0].lower()


async def test_the_provider_is_off_without_its_settings(env: Env) -> None:
    for path in ("/oidc/authorize", "/oidc/jwks", "/.well-known/openid-configuration"):
        assert (await env.client.get(path)).status_code == 404
    assert (await env.client.post("/oidc/token")).status_code == 404
