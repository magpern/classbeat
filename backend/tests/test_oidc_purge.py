"""The hourly purge removes the provider's expired requests and used or expired codes, and keeps
the grants (ADR 0034 section 1)."""

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from authtools import Env
from classbeat import auth
from oidctools import oidc_env

CHALLENGE = "a" * 43


@pytest.fixture
async def oenv(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    async with oidc_env(app_database_url, kek_dir) as running:
        yield running


async def request_row(env: Env, name: str, *, expired: bool) -> None:
    await env.run(
        "INSERT INTO oidc_requests (browser_hash, redirect_uri, state, nonce, code_challenge, "
        "prompt_login, received_at, expires_at) VALUES (:b, 'https://p.example/cb', :s, 'n', :c, "
        "false, now() - make_interval(secs => :age + 60), "
        "now() - make_interval(secs => :age - 10))",
        b=hashlib.sha256(name.encode()).digest(), s=name, c=CHALLENGE, age=3600 if expired else 0,
    )  # fmt: skip


async def code_row(env: Env, user_id: object, name: str, *, expired: bool, used: bool) -> None:
    await env.run(
        "INSERT INTO oidc_codes (code_hash, user_id, client_id, redirect_uri, code_challenge, "
        "nonce, auth_time, created_at, expires_at, used_at) VALUES (:h, :u, 'classbeat-player', "
        "'https://p.example/cb', :c, 'n', now(), now() - make_interval(secs => :age + 60), "
        "now() - make_interval(secs => :age - 30), CASE WHEN :used THEN now() END)",
        h=hashlib.sha256(name.encode()).digest(), u=user_id, c=CHALLENGE,
        age=3600 if expired else 0, used=used,
    )  # fmt: skip


async def test_purge_removes_expired_requests_and_spent_codes_and_keeps_grants(oenv: Env) -> None:
    user_id = await oenv.user()
    await request_row(oenv, "old", expired=True)
    await request_row(oenv, "live", expired=False)
    await code_row(oenv, user_id, "expired", expired=True, used=False)
    await code_row(oenv, user_id, "used", expired=False, used=True)
    await code_row(oenv, user_id, "live", expired=False, used=False)
    await oenv.run(
        "INSERT INTO oidc_grants (user_id, client_id, consent_at) "
        "VALUES (:u, 'classbeat-player', now())",
        u=user_id,
    )
    counts = await auth.purge_expired(oenv.factory, oenv.settings)
    assert (counts["oidc_requests"], counts["oidc_codes"]) == (1, 2)
    assert await oenv.scalar("SELECT string_agg(state, ',') FROM oidc_requests") == "live"
    assert await oenv.scalar("SELECT count(*) FROM oidc_codes") == 1
    assert await oenv.scalar("SELECT count(*) FROM oidc_grants") == 1
    again = await auth.purge_expired(oenv.factory, oenv.settings)
    assert (again["oidc_requests"], again["oidc_codes"]) == (0, 0)
