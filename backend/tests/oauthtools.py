"""Fixtures and helpers of the OAuth tests: an app whose providers are the in-process fake."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from authtools import Env, running_env
from classbeat import auth
from flows import Browser
from oidcfake import FakeProviders
from smtpfake import SmtpFake

CLIENT_IDS = {"google": "google-client", "github": "github-client", "microsoft": "ms-client"}


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders(CLIENT_IDS)


@pytest.fixture
async def oenv(
    clean_auth: None, app_database_url: str, smtp: SmtpFake, fake: FakeProviders
) -> AsyncIterator[Env]:
    """A running app with all three providers on, talking to the fake and the SMTP fake."""
    secrets = {f"oauth_{name}_client_secret": fake.secret for name in CLIENT_IDS}
    ids = {f"oauth_{name}_client_id": value for name, value in CLIENT_IDS.items()}
    async with running_env(
        app_database_url,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
        **ids,
        **secrets,
    ) as env:
        env.app.state.oauth.transport = fake.transport
        yield env


async def make_invite(env: Env) -> str:
    async with env.db() as db:
        return await auth.create_invite(db, timedelta(days=1))


async def start(
    browser: Browser, provider: str, purpose: str = "sign_in", **body: Any
) -> httpx.Response:
    return await browser.post(f"/api/auth/oauth/{provider}/start", {"purpose": purpose, **body})


async def sign_in_with(
    browser: Browser,
    fake: FakeProviders,
    provider: str,
    *,
    purpose: str = "sign_in",
    invite: str | None = None,
    return_to: str | None = None,
    **complete: Any,
) -> httpx.Response:
    """Start, approve at the fake provider, and follow the callback. Returns the 303."""
    started = await start(browser, provider, purpose, invite_code=invite, return_to=return_to)
    assert started.status_code == 200, started.text
    callback = fake.complete(started.json()["authorization_url"], **complete)
    return await browser.get(callback)


async def sign_up_with(
    browser: Browser, fake: FakeProviders, provider: str, **kwargs: Any
) -> httpx.Response:
    """``sign_in_with`` from the sign-up screen: the only purpose that may create an account."""
    return await sign_in_with(browser, fake, provider, purpose="sign_up", **kwargs)


async def drop_password(env: Env, email: str = "me@example.com") -> None:
    """Make the account a provider-only one (no password), as a Google sign-up leaves it."""
    await env.run("UPDATE users SET password_hash = NULL WHERE email = :email", email=email)


async def add_password(env: Env, email: str = "me@example.com") -> None:
    """Give the account a password (its value is never used here)."""
    hashed = await env.app.state.password_hasher.hash("another long passphrase")
    await env.run(
        "UPDATE users SET password_hash = :hash WHERE email = :email", hash=hashed, email=email
    )


def location(response: httpx.Response) -> str:
    assert response.status_code == 303, response.text
    return response.headers["location"]
