"""Every refusal of the OpenID Connect provider (ADR 0034 section 1, #1056 criterion 2)."""

import base64
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from authtools import Env
from oidctools import (
    CLIENT_ID,
    CLIENT_SECRET,
    REDIRECT_URI,
    FakePlayer,
    basic,
    flow_cookie_of,
    oidc_env,
    query_of,
)

PASSWORD = "argon2-test-hash"  # only whether there is one matters here


@pytest.fixture
async def oenv(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    async with oidc_env(app_database_url, kek_dir) as running:
        yield running


async def signed_in(oenv: Env, **kwargs: Any) -> tuple[UUID, str]:
    user_id = await oenv.user(**kwargs)
    return user_id, (await oenv.login(user_id)).token


async def events(oenv: Env, kind: str) -> list[Any]:
    async with oenv.db() as db:
        from sqlalchemy import text

        rows = await db.execute(
            text("SELECT user_id, detail FROM auth_events WHERE kind = :k ORDER BY at, id"),
            {"k": kind},
        )
        return list(rows.all())


# --- authorize: a bad client or redirect address is a page, never a redirect -------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "someone-else"},
        {"client_id": None},
        {"redirect_uri": "https://evil.example/cb"},
        {"redirect_uri": REDIRECT_URI + "/"},
        {"redirect_uri": None},
    ],
)
async def test_a_bad_client_or_redirect_is_an_error_page(
    oenv: Env, overrides: dict[str, str | None]
) -> None:
    answer = await FakePlayer(oenv).authorize(**overrides)
    assert answer.status_code == 400
    assert "location" not in answer.headers
    assert "evil.example" not in answer.text
    assert REDIRECT_URI not in answer.text
    assert answer.headers["cache-control"] == "no-store"
    assert await oenv.scalar("SELECT count(*) FROM oidc_requests") == 0


async def test_a_repeated_redirect_uri_is_an_error_page(oenv: Env) -> None:
    player = FakePlayer(oenv)
    query = "&".join(f"{k}={v}" for k, v in player.params().items())
    answer = await oenv.client.get(f"/oidc/authorize?{query}&redirect_uri=https://evil.example/")
    assert answer.status_code == 400
    assert "location" not in answer.headers


@pytest.mark.parametrize(
    "overrides",
    [
        {"prompt": "none"},
        {"prompt": "consent"},
        {"prompt": "select_account"},
        {"prompt": "login consent"},
        {"prompt": ""},
        {"max_age": "0"},
        {"response_type": "token"},
        {"code_challenge_method": "plain"},
        {"code_challenge": "short"},
        {"code_challenge": None},
        {"nonce": None},
        {"scope": "email"},
        {"scope": "openid profile"},
    ],
)
async def test_other_problems_redirect_with_invalid_request(
    oenv: Env, overrides: dict[str, str | None]
) -> None:
    player = FakePlayer(oenv)
    answer = await player.authorize(**overrides)
    assert answer.status_code == 302
    location = answer.headers["location"]
    assert location.startswith(REDIRECT_URI + "?")
    assert query_of(location) == {"error": "invalid_request", "state": player.state}
    assert await oenv.scalar("SELECT count(*) FROM oidc_requests") == 0


# --- continue and consent: the flow cookie binds the request to the browser --------------------


async def test_continue_without_the_flow_cookie_is_the_expired_page(oenv: Env) -> None:
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    started = await player.authorize()
    answer = await player.follow(started.headers["location"], None, session)
    assert answer.status_code == 400
    assert "location" not in answer.headers
    assert "expired" in answer.text
    assert len(await events(oenv, "oidc_request_refused")) == 1
    assert await oenv.scalar("SELECT count(*) FROM oidc_requests") == 1  # nothing deleted


async def test_continue_with_another_browsers_cookie_is_the_expired_page(oenv: Env) -> None:
    _, session = await signed_in(oenv)
    mine, theirs = FakePlayer(oenv), FakePlayer(oenv)
    started = await mine.authorize()
    other = flow_cookie_of(await theirs.authorize())
    answer = await mine.follow(started.headers["location"], other, session)
    assert answer.status_code == 400
    assert "expired" in answer.text
    assert len(await events(oenv, "oidc_request_refused")) == 1


async def test_continue_with_an_expired_or_unknown_request_is_the_expired_page(oenv: Env) -> None:
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    await oenv.run(
        "UPDATE oidc_requests SET received_at = now() - interval '20 minutes', "
        "expires_at = now() - interval '10 minutes'"
    )
    expired = await player.follow(started.headers["location"], flow, session)
    assert expired.status_code == 400
    assert "expired" in expired.text
    unknown = await player.follow(
        "/oidc/authorize/continue?r=00000000-0000-4000-8000-000000000000", flow, session
    )
    assert unknown.status_code == 400
    assert "expired" in unknown.text


async def test_continue_signed_out_goes_to_sign_in_and_back(oenv: Env) -> None:
    player = FakePlayer(oenv)
    started = await player.authorize()
    answer = await player.follow(started.headers["location"], flow_cookie_of(started), None)
    assert answer.status_code == 302
    location = answer.headers["location"]
    assert location.startswith("/signin?return_to=%2Foidc%2Fauthorize%2Fcontinue%3Fr%3D")


async def test_consent_without_or_with_another_browsers_cookie_is_refused(oenv: Env) -> None:
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    shown = await player.follow(started.headers["location"], flow, session)
    request_id = query_of(shown.headers["location"])["r"]
    other = flow_cookie_of(await FakePlayer(oenv).authorize())
    for cookie in (None, other):
        answer = await player.consent(request_id, cookie, session)
        assert answer.status_code == 400
        assert answer.json() == {"detail": {"reason": "request_expired"}}
    assert await oenv.scalar("SELECT count(*) FROM oidc_codes") == 0
    assert (await player.consent(request_id, flow, session)).status_code == 200  # still there


async def test_consent_twice_issues_one_code(oenv: Env) -> None:
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    shown = await player.follow(started.headers["location"], flow, session)
    request_id = query_of(shown.headers["location"])["r"]
    assert (await player.consent(request_id, flow, session)).status_code == 200
    again = await player.consent(request_id, flow, session)
    assert again.status_code == 400
    assert await oenv.scalar("SELECT count(*) FROM oidc_codes") == 1


async def test_a_disabled_user_cannot_continue_or_redeem(oenv: Env) -> None:
    user_id, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    code = await player.code_for(session)
    await oenv.run("UPDATE users SET disabled_at = now() WHERE id = :u", u=user_id)
    answer = await player.token(code)
    assert answer.status_code == 400
    assert answer.json() == {"error": "invalid_grant"}
    assert [e.detail for e in await events(oenv, "oidc_code_refused")] == [
        {"reason": "user_inactive"}
    ]
    assert await events(oenv, "oidc_signed_in") == []
    # A disabled user's session is no session: continue sends them to sign in.
    again = FakePlayer(oenv)
    started = await again.authorize()
    shown = await again.follow(started.headers["location"], flow_cookie_of(started), session)
    assert shown.headers["location"].startswith("/signin?")


# --- prompt=login: a re-authentication after the request arrived --------------------------------


async def test_prompt_login_needs_a_reauthentication_after_the_request(oenv: Env) -> None:
    user_id, session = await signed_in(oenv, password_hash=PASSWORD)
    await FakePlayer(oenv).code_for(session)  # consented before
    await oenv.run("DELETE FROM oidc_codes")
    player = FakePlayer(oenv)
    started = await player.authorize(prompt="login")
    flow = flow_cookie_of(started)
    shown = await player.follow(started.headers["location"], flow, session)
    assert shown.headers["location"].startswith("/player-consent?r=")  # no code without it
    request_id = query_of(shown.headers["location"])["r"]
    refused = await player.consent(request_id, flow, session)
    assert refused.status_code == 403
    assert refused.json()["detail"] == {"reason": "reauth_required"}
    assert await oenv.scalar("SELECT count(*) FROM oidc_codes") == 0
    await oenv.run("UPDATE sessions SET reauth_at = now() WHERE user_id = :u", u=user_id)
    given = await player.consent(request_id, flow, session)
    assert given.status_code == 200, given.text
    code = query_of(given.json()["redirect"])["code"]
    claims = await player.verify((await player.token(code)).json()["id_token"])
    received = await oenv.scalar(
        "SELECT max(reauth_at) FROM sessions WHERE user_id = :u", u=user_id
    )
    assert claims["auth_time"] == int(received.timestamp())  # pyright: ignore[reportAttributeAccessIssue]


async def test_prompt_login_without_a_way_to_reauthenticate_is_login_required(oenv: Env) -> None:
    _, session = await signed_in(oenv)  # no password, provider, passkey or TOTP
    await FakePlayer(oenv).code_for(session)
    player = FakePlayer(oenv)
    started = await player.authorize(prompt="login")
    shown = await player.follow(started.headers["location"], flow_cookie_of(started), session)
    query = query_of(shown.headers["location"])
    assert shown.headers["location"].startswith(REDIRECT_URI + "?")
    assert query["error"] == "login_required"
    assert query["state"] == player.state
    assert "password or a passkey" in query["error_description"]


# --- the token endpoint -------------------------------------------------------------------------


async def test_a_code_works_once(oenv: Env) -> None:
    user_id, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    code = await player.code_for(session)
    assert (await player.token(code)).status_code == 200
    again = await player.token(code)
    assert again.status_code == 400
    assert again.json() == {"error": "invalid_grant"}
    assert again.headers["cache-control"] == "no-store"
    refused = await events(oenv, "oidc_code_refused")
    assert [(r.user_id, r.detail) for r in refused] == [(user_id, {"reason": "used"})]
    unknown = await player.token("x" * 43)
    assert unknown.json() == {"error": "invalid_grant"}


async def test_an_expired_code_is_refused_and_burnt(oenv: Env) -> None:
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    code = await player.code_for(session)
    await oenv.run(
        "UPDATE oidc_codes SET created_at = now() - interval '2 minutes', "
        "expires_at = now() - interval '1 minute'"
    )
    answer = await player.token(code)
    assert answer.status_code == 400
    assert answer.json() == {"error": "invalid_grant"}
    assert [e.detail for e in await events(oenv, "oidc_code_refused")] == [{"reason": "expired"}]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"verifier": "w" * 64}, "pkce"),
        ({"verifier": "short"}, "pkce"),
        ({"redirect_uri": "https://player.example/other"}, "redirect_uri"),
    ],
)
async def test_a_pkce_or_redirect_mismatch_burns_the_code(
    oenv: Env, change: dict[str, Any], reason: str
) -> None:
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    code = await player.code_for(session)
    answer = await player.token(code, **change)
    assert answer.status_code == 400
    assert answer.json() == {"error": "invalid_grant"}
    assert [e.detail for e in await events(oenv, "oidc_code_refused")] == [{"reason": reason}]
    assert (await player.token(code)).json() == {"error": "invalid_grant"}  # burnt


def _basic(raw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(raw.encode()).decode()}


@pytest.mark.parametrize(
    ("headers", "extra"),
    [
        (basic(secret="x" * 43), None),
        (basic(secret=CLIENT_SECRET[:-1]), None),
        (basic(client_id="someone-else"), None),
        (_basic(CLIENT_ID + CLIENT_SECRET), None),  # no colon
        ({"Authorization": "Basic !!!"}, None),
        ({"Authorization": f"Bearer {CLIENT_SECRET}"}, None),
        ({}, {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}),  # in the body
        (basic(), {"client_secret": CLIENT_SECRET}),  # in both
        (basic(), {"client_id": "someone-else"}),
    ],
)
async def test_a_wrong_client_or_secret_is_invalid_client(
    oenv: Env, headers: dict[str, str], extra: dict[str, str] | None
) -> None:
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    code = await player.code_for(session)
    answer = await player.token(code, headers=headers or {"X-None": "1"}, extra=extra)
    assert answer.status_code == 401
    assert answer.json() == {"error": "invalid_client"}
    assert answer.headers["www-authenticate"].startswith("Basic")
    assert answer.headers["cache-control"] == "no-store"
    assert len(await events(oenv, "oidc_client_auth_failed")) == 1
    assert (await player.token(code)).status_code == 200  # the code was not touched


async def test_the_token_endpoint_refuses_other_grants_and_bodies(oenv: Env) -> None:
    player = FakePlayer(oenv)
    other = await player.token("c", extra={"grant_type": "refresh_token"})
    assert other.status_code == 400
    assert other.json() == {"error": "unsupported_grant_type"}
    as_json = await oenv.client.post("/oidc/token", json={"grant_type": "x"}, headers=basic())
    assert as_json.status_code == 400
    assert as_json.json() == {"error": "invalid_request"}
    large = await oenv.client.post(
        "/oidc/token",
        content=b"a=" + b"x" * 9000,
        headers={"Content-Type": "application/x-www-form-urlencoded", **basic()},
    )
    assert large.status_code == 413


async def test_the_token_endpoint_needs_no_origin_or_csrf(oenv: Env) -> None:
    """ADR 0034 amends ADR 0003 section 4 for this one path; the consent POST keeps both."""
    _, session = await signed_in(oenv)
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    shown = await player.follow(started.headers["location"], flow, session)
    request_id = query_of(shown.headers["location"])["r"]
    from authtools import session_cookie

    bare = await oenv.client.post(
        "/oidc/authorize/consent", json={"r": request_id, "allow": True},
        headers=session_cookie(session),
    )  # fmt: skip
    oenv.client.cookies.clear()
    assert bare.status_code == 403
    code = query_of((await player.consent(request_id, flow, session)).json()["redirect"])["code"]
    assert (await player.token(code)).status_code == 200  # no Origin, no CSRF token
