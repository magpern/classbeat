"""Helpers for the passkey route tests (ADR 0019, #772): an app with passkeys on, a signed-in
browser, the register ceremony against the software authenticator, planted passkeys."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest

from authtools import Env, running_env
from flows import Browser, user_with_password
from smtpfake import Received, SmtpFake
from webauthnfake import RP_ID, SoftAuthenticator

EMAIL = "user@example.com"
START = "/api/auth/passkeys/register/start"
FINISH = "/api/auth/passkeys/register/finish"
REGISTER_COOKIE = "__Host-classbeat_passkey_register"
# A trusted loopback proxy, so a test can move the client with ``X-Forwarded-For`` (#632).
HOME, MOBILE = "198.51.100.7", "203.0.113.40"


class Client(Browser):
    """A browser with a client address (``X-Forwarded-For`` behind the trusted loopback proxy)."""

    def __init__(self, env: Env, ip: str = HOME) -> None:
        super().__init__(env, ip)
        self.address = ip

    def _cookie_header(self) -> dict[str, str]:
        return {**super()._cookie_header(), "X-Forwarded-For": self.address}


@pytest.fixture
async def penv(clean_auth: None, app_database_url: str, smtp: SmtpFake) -> AsyncIterator[Env]:
    """The app with ``CLASSBEAT_PASSKEYS`` on (RP ID ``classbeat.test``) and the SMTP fake."""
    async with running_env(
        app_database_url,
        passkeys=True,
        webauthn_rp_id=RP_ID,
        trusted_proxies="127.0.0.1",
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
    ) as running:
        yield running


async def signed_in(env: Env, email: str = EMAIL, ip: str = HOME) -> Client:
    """A user with a password, signed in just now (so recently authenticated)."""
    client = Client(env, ip)
    await user_with_password(env, email)
    assert (await client.sign_in(email)).status_code == 200
    return client


async def second_browser(env: Env, email: str = EMAIL) -> Client:
    """Another browser (its own session) of an existing user."""
    client = Client(env)
    assert (await client.sign_in(email)).status_code == 200
    return client


async def start(client: Browser) -> httpx.Response:
    return await client.post(START)


async def finish(client: Browser, credential: dict[str, Any], **extra: Any) -> httpx.Response:
    return await client.post(FINISH, {"credential": credential, **extra})


async def register(
    client: Browser, authenticator: SoftAuthenticator | None = None, **extra: Any
) -> httpx.Response:
    """A whole registration; returns the finish response."""
    begun = await start(client)
    assert begun.status_code == 200, begun.text
    device = authenticator or SoftAuthenticator()
    return await finish(client, device.create(begun.json()), **extra)


async def user_id_of(env: Env, email: str = EMAIL) -> UUID:
    return await env.scalar("SELECT id FROM users WHERE email = :e", e=email)  # type: ignore[return-value]


async def plant(env: Env, user_id: UUID, label: str = "Planted") -> UUID:
    """A passkey row written directly (for the list, rename and remove tests)."""
    return await env.scalar(  # type: ignore[return-value]
        "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, backup_eligible, "
        "backup_state, label, transports) VALUES (:u, :c, :k, true, true, :l, '{internal}') "
        "RETURNING id",
        u=user_id,
        c=secrets.token_bytes(16),
        k=b"\xa1\x01\x02",
        l=label,
    )


async def count(env: Env, sql: str, **params: object) -> int:
    return int(await env.scalar(sql, **params))  # type: ignore[arg-type]


async def mails(smtp: SmtpFake, address: str, n: int = 1) -> list[Received]:
    """The mails to ``address`` once ``n`` have arrived (the notices are background tasks)."""
    for _ in range(200):
        found = smtp.to(address)
        if len(found) >= n:
            return found
        await asyncio.sleep(0.01)
    return smtp.to(address)


def reason(response: httpx.Response) -> str:
    return response.json()["detail"]["reason"]


def cleared(response: httpx.Response, name: str) -> bool:
    found = response.headers.get_list("set-cookie")
    return any(c.startswith(f"{name}=") and "Max-Age=0" in c for c in found)
