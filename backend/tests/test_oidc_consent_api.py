"""``/api/auth/player-consent``: the account page reads and withdraws the player consent
(ADR 0034 sections 1 and 6)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from authtools import Env, running_env, session_cookie, unsafe_headers
from oidctools import FakePlayer, oidc_env

PATH = "/api/auth/player-consent"


@pytest.fixture
async def oenv(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    async with oidc_env(app_database_url, kek_dir) as running:
        yield running


async def csrf_of(env: Env, token: str) -> str:
    info = await env.client.get("/api/auth/session", headers=session_cookie(token))
    return info.json()["csrf_token"]


async def test_read_and_withdraw_the_consent(oenv: Env) -> None:
    user_id = await oenv.user()
    session = (await oenv.login(user_id)).token
    assert (await oenv.client.get(PATH, headers=session_cookie(session))).json() == {
        "enabled": True, "consent_at": None
    }  # fmt: skip
    player = FakePlayer(oenv)
    assert (await player.token(await player.code_for(session))).status_code == 200
    given = (await oenv.client.get(PATH, headers=session_cookie(session))).json()
    assert given["enabled"]
    assert given["consent_at"] is not None

    csrf = await csrf_of(oenv, session)
    gone = await oenv.client.delete(PATH, headers=unsafe_headers(session, csrf))
    assert gone.json() == {"withdrawn": True}
    assert (await oenv.client.get(PATH, headers=session_cookie(session))).json()[
        "consent_at"
    ] is None
    assert (
        await oenv.scalar(
            "SELECT count(*) FROM auth_events "
            "WHERE kind = 'oidc_consent_withdrawn' AND user_id = :u",
            u=user_id,
        )
        == 1
    )
    assert await oenv.scalar("SELECT count(*) FROM oidc_grants WHERE user_id = :u", u=user_id) == 1
    again = await oenv.client.delete(PATH, headers=unsafe_headers(session, csrf))
    assert again.json() == {"withdrawn": False}


async def test_withdrawing_leaves_another_users_consent_alone(oenv: Env) -> None:
    sessions: list[str] = []
    for email in ("alice@example.com", "bob@example.com"):
        session = (await oenv.login(await oenv.user(email))).token
        player = FakePlayer(oenv)
        assert (await player.token(await player.code_for(session))).status_code == 200
        sessions.append(session)
    alice, bob = sessions

    gone = await oenv.client.delete(PATH, headers=unsafe_headers(alice, await csrf_of(oenv, alice)))
    assert gone.json() == {"withdrawn": True}
    assert (await oenv.client.get(PATH, headers=session_cookie(alice))).json()["consent_at"] is None
    kept = (await oenv.client.get(PATH, headers=session_cookie(bob))).json()
    assert kept["consent_at"] is not None


async def test_the_routes_need_a_session_and_csrf(oenv: Env) -> None:
    assert (await oenv.client.get(PATH)).status_code == 401
    session = (await oenv.login(await oenv.user())).token
    refused = await oenv.client.delete(PATH, headers=unsafe_headers(session))
    assert refused.status_code == 403


async def test_with_the_provider_off_nothing_is_enabled(
    clean_auth: None, app_database_url: str
) -> None:
    async with running_env(app_database_url) as env:
        session = (await env.login(await env.user())).token
        assert (await env.client.get(PATH, headers=session_cookie(session))).json() == {
            "enabled": False, "consent_at": None
        }  # fmt: skip
        csrf = await csrf_of(env, session)
        gone = await env.client.delete(PATH, headers=unsafe_headers(session, csrf))
        assert gone.json() == {"withdrawn": False}
