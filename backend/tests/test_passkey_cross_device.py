"""A cross-device passkey sign-in gets a shorter session (ADR 0019, owner question 4, W9, #776).

An assertion reporting ``authenticatorAttachment: "cross-platform"`` (a phone over QR, or a
security key) gives a session with a 24-hour absolute limit and no known-device cookie; a later
rotation or sign-in in that browser keeps the shorter limit. The attachment is client-reported,
so it only ever makes things stricter: missing or ``platform`` is the normal sign-in.
"""

from typing import Any
from uuid import UUID

import httpx
import pytest

from authtools import Env
from classbeat.auth.passkey_attachment_policy import CROSS_DEVICE_SESSION, is_cross_platform
from flows import PASSWORD
from passkeysignin import anonymous, failures, sign_in_with_passkey, with_passkey
from passkeytools import Client, count
from passkeytools import penv as penv
from webauthnfake import MISSING

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

DAY = 24 * 3600
SESSION_COOKIE = "__Host-classbeat_session"
DEVICE_COOKIE = "__Host-classbeat_device"
DEVICES = "SELECT count(*) FROM known_devices WHERE user_id = :u"


@pytest.mark.parametrize(
    ("credential", "expected"),
    [
        ({"authenticatorAttachment": "cross-platform"}, True),
        ({"authenticatorAttachment": "platform"}, False),
        ({}, False),
        ({"authenticatorAttachment": None}, False),
        ({"authenticatorAttachment": "Cross-Platform"}, False),
        ({"authenticatorAttachment": ["cross-platform"]}, False),
        ({"authenticatorAttachment": 1}, False),
    ],
)
def test_only_the_exact_cross_platform_value_counts(
    credential: dict[str, Any], expected: bool
) -> None:
    assert is_cross_platform(credential) is expected


def test_the_limit_is_24_hours() -> None:
    assert CROSS_DEVICE_SESSION.total_seconds() == DAY


async def remaining(env: Env, user_id: UUID) -> float:
    """Seconds left on the user's one session (absolute limit)."""
    value = await env.scalar(
        "SELECT extract(epoch FROM expires_at - now()) FROM sessions WHERE user_id = :u",
        u=user_id,
    )
    return float(value)  # type: ignore[arg-type]


def cookie(response: httpx.Response, name: str) -> str | None:
    return next(
        (c for c in response.headers.get_list("set-cookie") if c.startswith(f"{name}=")), None
    )


def max_age(header: str) -> int:
    part = next(p for p in header.split(";") if p.strip().lower().startswith("max-age="))
    return int(part.split("=", 1)[1])


async def test_a_cross_platform_sign_in_lasts_24_hours_and_sets_no_device_cookie(
    penv: Env,
) -> None:
    holder = await with_passkey(penv)
    holder.device.attachment = "cross-platform"
    devices = await count(penv, DEVICES, u=holder.user_id)
    client = anonymous(penv)
    response = await sign_in_with_passkey(client, holder)
    assert response.status_code == 200, response.text
    assert DAY - 120 < await remaining(penv, holder.user_id) <= DAY
    session = cookie(response, SESSION_COOKIE)
    assert session is not None
    assert DAY - 120 < max_age(session) <= DAY
    assert cookie(response, DEVICE_COOKIE) is None
    assert DEVICE_COOKIE not in client.cookies
    assert await count(penv, DEVICES, u=holder.user_id) == devices  # no known device made
    assert (await client.get("/api/auth/session")).json()["authenticated"] is True


@pytest.mark.parametrize("attachment", ["platform", MISSING, None])
async def test_a_platform_or_unreported_sign_in_is_unchanged(penv: Env, attachment: Any) -> None:
    holder = await with_passkey(penv)
    holder.device.attachment = attachment
    client = anonymous(penv)
    response = await sign_in_with_passkey(client, holder)
    assert response.status_code == 200, response.text
    assert await remaining(penv, holder.user_id) > 29 * DAY  # the normal 30 days
    assert cookie(response, DEVICE_COOKIE) is not None
    assert DEVICE_COOKIE in client.cookies


async def test_an_unknown_attachment_string_is_refused_not_trusted(penv: Env) -> None:
    """py_webauthn refuses it while parsing, before anything reads it: the generic failure."""
    holder = await with_passkey(penv)
    holder.device.attachment = "phone"
    response = await sign_in_with_passkey(anonymous(penv), holder)
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "passkey_failed"
    assert await failures(penv, "format") == 1
    assert (
        await count(penv, "SELECT count(*) FROM sessions WHERE user_id = :u", u=holder.user_id) == 0
    )


async def test_a_rotation_keeps_the_shorter_expiry(penv: Env) -> None:
    holder = await with_passkey(penv)
    holder.device.attachment = "cross-platform"
    client = anonymous(penv)
    assert (await sign_in_with_passkey(client, holder)).status_code == 200
    before = await penv.scalar(
        "SELECT expires_at FROM sessions WHERE user_id = :u", u=holder.user_id
    )
    # A password re-auth rotates the session (``rotate_session``): a new row and token.
    response = await client.post("/api/auth/reauth", {"password": PASSWORD})
    assert response.status_code == 200, response.text
    after = await penv.scalar(
        "SELECT expires_at FROM sessions WHERE user_id = :u", u=holder.user_id
    )
    assert after == before
    rotated = cookie(response, SESSION_COOKIE)
    assert rotated is not None
    assert max_age(rotated) <= DAY


async def test_a_platform_passkey_re_auth_keeps_the_shorter_expiry(penv: Env) -> None:
    holder = await with_passkey(penv)
    holder.device.attachment = "cross-platform"
    client = anonymous(penv)
    assert (await sign_in_with_passkey(client, holder)).status_code == 200
    before = await penv.scalar(
        "SELECT expires_at FROM sessions WHERE user_id = :u", u=holder.user_id
    )
    holder.device.attachment = "platform"
    begun = await client.post("/api/auth/passkeys/reauth/start")
    assert begun.status_code == 200, begun.text
    finished = await client.post(
        "/api/auth/passkeys/reauth/finish", {"credential": holder.device.get(begun.json(), None)}
    )
    assert finished.status_code == 200, finished.text
    after = await penv.scalar(
        "SELECT expires_at FROM sessions WHERE user_id = :u", u=holder.user_id
    )
    assert after == before


async def test_a_later_sign_in_in_that_browser_keeps_the_shorter_expiry(penv: Env) -> None:
    holder = await with_passkey(penv)
    holder.device.attachment = "cross-platform"
    client = anonymous(penv)
    assert (await sign_in_with_passkey(client, holder)).status_code == 200
    before = await penv.scalar(
        "SELECT expires_at FROM sessions WHERE user_id = :u", u=holder.user_id
    )
    holder.device.attachment = "platform"
    response = await sign_in_with_passkey(client, holder)  # replaces the browser's own session
    assert response.status_code == 200, response.text
    after = await penv.scalar(
        "SELECT expires_at FROM sessions WHERE user_id = :u", u=holder.user_id
    )
    assert after == before
    assert (await client.sign_in(holder.email, PASSWORD)).status_code == 200
    again = await penv.scalar(
        "SELECT expires_at FROM sessions WHERE user_id = :u", u=holder.user_id
    )
    assert again == before


async def test_a_cross_platform_sign_in_caps_an_inherited_longer_session(penv: Env) -> None:
    holder = await with_passkey(penv)
    client = Client(penv)
    assert (await client.sign_in(holder.email, PASSWORD)).status_code == 200
    assert await remaining(penv, holder.user_id) > 29 * DAY
    holder.device.attachment = "cross-platform"
    assert (await sign_in_with_passkey(client, holder)).status_code == 200
    assert DAY - 120 < await remaining(penv, holder.user_id) <= DAY


async def test_a_cross_platform_sign_in_never_lengthens_a_shorter_session(penv: Env) -> None:
    holder = await with_passkey(penv)
    client = Client(penv)
    assert (await client.sign_in(holder.email, PASSWORD)).status_code == 200
    await penv.run(
        "UPDATE sessions SET expires_at = now() + interval '1 hour' WHERE user_id = :u",
        u=holder.user_id,
    )
    holder.device.attachment = "cross-platform"
    assert (await sign_in_with_passkey(client, holder)).status_code == 200
    assert await remaining(penv, holder.user_id) <= 3600
