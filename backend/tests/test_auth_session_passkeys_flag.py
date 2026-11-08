"""``GET /api/auth/session`` tells the pages whether passkeys are on (ADR 0019 section 1): the
sign-in and re-auth pages show no passkey UI while ``CLASSBEAT_PASSKEYS`` is off. The flag is the
same for everyone, so an anonymous caller may read it."""

from authtools import Env
from passkeytools import penv as penv


async def test_the_flag_is_off_by_default(env: Env) -> None:
    body = (await env.client.get("/api/auth/session")).json()
    assert body["authenticated"] is False
    assert body["passkeys"] is False


async def test_the_flag_is_on_when_passkeys_are_on(penv: Env) -> None:
    body = (await penv.client.get("/api/auth/session")).json()
    assert body["authenticated"] is False
    assert body["passkeys"] is True
