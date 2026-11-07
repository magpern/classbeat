"""Helpers for the passkey sign-in tests (ADR 0019 section 5, #773): a user with a passkey
registered through the real ceremony, and the sign-in ceremony against the software
authenticator, with every field bendable."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from authtools import Env
from passkeytools import HOME, Client, count, register, signed_in, user_id_of
from webauthnfake import SoftAuthenticator

SIGNIN_START = "/api/auth/passkeys/signin/start"
SIGNIN_FINISH = "/api/auth/passkeys/signin/finish"
SIGNIN_COOKIE = "__Host-classbeat_passkey_signin"
SESSIONS = "SELECT count(*) FROM sessions"


@dataclass
class Holder:
    """A user, their passkey on a software authenticator and their user handle."""

    user_id: UUID
    email: str
    device: SoftAuthenticator
    handle: bytes
    browser: Client  # signed out again, but a known device of the user (its device cookie)


async def with_passkey(
    env: Env, email: str = "user@example.com", device: SoftAuthenticator | None = None
) -> Holder:
    """Register a passkey for a new user through the real ceremony, then sign that browser out."""
    client = await signed_in(env, email)
    authenticator = device or SoftAuthenticator()
    finished = await register(client, authenticator)
    assert finished.status_code == 201, finished.text
    await client.post("/api/auth/logout")
    user = await user_id_of(env, email)
    handle = await env.scalar("SELECT webauthn_user_handle FROM users WHERE id = :u", u=user)
    assert isinstance(handle, bytes)
    return Holder(user, email, authenticator, handle, client)


async def start_signin(client: Client) -> httpx.Response:
    return await client.post(SIGNIN_START)


async def finish_signin(client: Client, credential: dict[str, Any]) -> httpx.Response:
    return await client.post(SIGNIN_FINISH, {"credential": credential})


async def sign_in_with_passkey(
    client: Client, holder: Holder, *, handle: bytes | str | None = "own", **bend: Any
) -> httpx.Response:
    """A whole sign-in in ``client``; ``handle`` "own" sends the holder's user handle."""
    begun = await start_signin(client)
    assert begun.status_code == 200, begun.text
    user_handle = holder.handle if handle == "own" else handle
    assert not isinstance(user_handle, str)
    return await finish_signin(client, holder.device.get(begun.json(), user_handle, **bend))


def anonymous(env: Env, ip: str = HOME) -> Client:
    return Client(env, ip)


async def failures(env: Env, reason: str) -> int:
    """``sign_in_failed`` passkey events with this category."""
    return await count(
        env,
        "SELECT count(*) FROM auth_events WHERE kind = 'sign_in_failed' "
        "AND detail->>'method' = 'passkey' AND detail->>'reason' = :r",
        r=reason,
    )
