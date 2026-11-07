"""Re-authenticate with a passkey: start and finish (ADR 0019 sections 6, 7, 8, 11 and 13, #774).

The software authenticator (``webauthnfake``) builds real assertions; no network (netguard). The
lock-order and reset-race tests are in ``test_passkey_locks``.
"""

from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from authtools import Env, running_env
from flows import PASSWORD
from passkeysignin import with_passkey
from passkeytools import (
    EMAIL,
    MOBILE,
    REGISTER_COOKIE,
    Client,
    cleared,
    count,
    mails,
    reason,
    register,
    second_browser,
    signed_in,
    start,
    user_id_of,
)
from passkeytools import penv as penv
from smtpfake import SmtpFake
from webauthnfake import RP_ID, SoftAuthenticator, b64url

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

REAUTH_START = "/api/auth/passkeys/reauth/start"
REAUTH_FINISH = "/api/auth/passkeys/reauth/finish"
REAUTH_COOKIE = "__Host-classbeat_passkey_reauth"
OLD = "2026-01-01T00:00:00+00:00"
REFUSED_MAIL = "A passkey sign-in to your classbeat account was refused"
EVENT = "SELECT {0} FROM auth_events WHERE kind = :k AND detail->>'method' = 'passkey'"


async def ready(env: Env, email: str = EMAIL) -> tuple[Client, SoftAuthenticator]:
    """A signed-in user with a passkey registered in this browser."""
    client = await signed_in(env, email)
    device = SoftAuthenticator()
    assert (await register(client, device)).status_code == 201
    return client, device


async def finish(client: Client, credential: dict[str, Any]) -> httpx.Response:
    return await client.post(REAUTH_FINISH, {"credential": credential})


async def reauth(client: Client, device: SoftAuthenticator, **bend: Any) -> httpx.Response:
    begun = await client.post(REAUTH_START)
    assert begun.status_code == 200, begun.text
    return await finish(client, device.get(begun.json(), None, **bend))


async def session(env: Env, user_id: UUID) -> Any:
    """The user's one session row."""
    async with env.db() as db:
        return (
            await db.execute(
                text(
                    "SELECT id, reauth_at, stepup_at, mfa_at, location_changed_at FROM sessions "
                    "WHERE user_id = :u"
                ),
                {"u": user_id},
            )
        ).one()


async def failed(env: Env, category: str) -> int:
    return await count(
        env,
        "SELECT count(*) FROM auth_events WHERE kind = 'reauth_failed' "
        "AND detail->>'method' = 'passkey' AND detail->>'reason' = :r",
        r=category,
    )


async def passkey_id(env: Env, user_id: UUID) -> UUID:
    return await env.scalar("SELECT id FROM webauthn_credentials WHERE user_id = :u", u=user_id)  # type: ignore[return-value]


async def stale(env: Env, user_id: UUID) -> None:
    """No recent authentication; old step-up and MFA times, to see that they are kept."""
    await env.run(
        "UPDATE sessions SET reauth_at = NULL, stepup_at = :t, mfa_at = :t WHERE user_id = :u",
        t=OLD,
        u=user_id,
    )


# --- start --------------------------------------------------------------------------------------


async def test_start_names_the_users_own_passkeys_and_binds_the_flow_to_the_session(
    penv: Env,
) -> None:
    client, device = await ready(penv)
    await with_passkey(penv, "other@example.com")  # not offered
    user = await user_id_of(penv)
    response = await client.post(REAUTH_START)
    assert response.status_code == 200, response.text
    options = response.json()["publicKey"]
    assert options["rpId"] == RP_ID
    assert options["userVerification"] == "required"
    assert [c["id"] for c in options["allowCredentials"]] == [b64url(device.credential_id)]
    cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith(f"{REAUTH_COOKIE}=")
    )
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=300"):
        assert attribute.lower() in cookie.lower()
    bound = await penv.scalar(
        "SELECT user_id = :u AND session_id = (SELECT id FROM sessions WHERE user_id = :u) "
        "FROM webauthn_challenges WHERE purpose = 'reauth'",
        u=user,
    )
    assert bound is True


async def test_a_reauth_start_keeps_a_pending_registration(penv: Env) -> None:
    client, _ = await ready(penv)
    assert (await start(client)).status_code == 200
    assert (await client.post(REAUTH_START)).status_code == 200
    assert REGISTER_COOKIE in client.cookies
    assert await count(penv, "SELECT count(*) FROM webauthn_challenges") == 2


async def test_both_answer_feature_off_with_the_flag_off(
    clean_auth: None, app_database_url: str
) -> None:
    async with running_env(app_database_url) as env:
        client = await signed_in(env)
        for path in (REAUTH_START, REAUTH_FINISH):
            response = await client.post(path, {"credential": {}})
            assert (response.status_code, reason(response)) == (404, "feature_off")


# --- finish -------------------------------------------------------------------------------------


async def test_a_passkey_sets_reauth_at_only_and_rotates_the_session(penv: Env) -> None:
    client, device = await ready(penv)
    user = await user_id_of(penv)
    await stale(penv, user)
    before = await session(penv, user)
    old_cookie = dict(client.cookies)
    device.sign_count = 4
    response = await reauth(client, device)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}
    assert cleared(response, REAUTH_COOKIE)
    after = await session(penv, user)
    assert after.id != before.id  # rotated through rotate_session
    assert after.reauth_at is not None
    assert (after.stepup_at, after.mfa_at) == (before.stepup_at, before.mfa_at)  # never set
    state = (await client.get("/api/auth/session")).json()["session"]
    assert (state["recent_auth"], state["stepup"], state["mfa"]) == (True, False, False)
    old = Client(penv)
    old.cookies = old_cookie
    assert (await old.get("/api/auth/session")).json()["authenticated"] is False
    detail = await penv.scalar(EVENT.format("detail"), k="reauth")
    assert detail == {"method": "passkey", "passkey": str(await passkey_id(penv, user))}
    stored = await penv.scalar("SELECT sign_count FROM webauthn_credentials")
    assert stored == 4
    assert await penv.scalar("SELECT last_used_at IS NOT NULL FROM webauthn_credentials") is True
    assert await count(penv, "SELECT count(*) FROM webauthn_challenges") == 0


async def test_another_sessions_flow_is_refused(penv: Env) -> None:
    client, device = await ready(penv)
    other = await second_browser(penv)
    begun = await client.post(REAUTH_START)
    other.cookies[REAUTH_COOKIE] = client.cookies[REAUTH_COOKIE]  # the flow moved to a session
    sessions = await count(penv, "SELECT count(*) FROM sessions")
    response = await finish(other, device.get(begun.json(), None))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert cleared(response, REAUTH_COOKIE)
    assert await failed(penv, "challenge") == 1
    assert await count(penv, "SELECT count(*) FROM sessions") == sessions
    assert await count(penv, EVENT.format("count(*)"), k="reauth") == 0


async def test_a_flow_of_a_session_that_rotated_since_is_refused(penv: Env) -> None:
    client, device = await ready(penv)
    begun = await client.post(REAUTH_START)
    # Another tab re-authenticates with the password: the session rotates (section 6).
    assert (await client.post("/api/auth/reauth", {"password": PASSWORD})).status_code == 200
    response = await finish(client, device.get(begun.json(), None))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failed(penv, "challenge") == 1
    assert await count(penv, EVENT.format("count(*)"), k="reauth") == 0


async def test_another_users_valid_passkey_is_refused_and_left_untouched(penv: Env) -> None:
    client, _ = await ready(penv)
    user = await user_id_of(penv)
    await stale(penv, user)
    before = await session(penv, user)
    victim = await with_passkey(penv, "other@example.com")
    victim.device.sign_count = 9  # would be stored, had the counter rule run on it
    begun = await client.post(REAUTH_START)
    response = await finish(client, victim.device.get(begun.json(), victim.handle))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failed(penv, "unknown") == 1
    untouched = await penv.scalar(
        "SELECT sign_count = 0 AND last_used_at IS NULL FROM webauthn_credentials "
        "WHERE user_id = :u",
        u=victim.user_id,
    )
    assert untouched is True
    after = await session(penv, user)
    assert (after.id, after.reauth_at) == (before.id, None)  # neither rotated nor re-authenticated


BENT = [
    ({"origin": "https://evil.example"}, "origin"),
    ({"rp_id": "evil.example"}, "rp_id"),
    ({"uv": False}, "uv"),
    ({"bad_signature": True}, "signature"),
    ({"challenge": b64url(b"x" * 32)}, "challenge"),
]


@pytest.mark.parametrize(("bend", "category"), BENT)
async def test_a_bent_assertion_is_refused_and_recorded_as_a_category(
    penv: Env, bend: dict[str, Any], category: str
) -> None:
    client, device = await ready(penv)
    user = await user_id_of(penv)
    await stale(penv, user)
    response = await reauth(client, device, **bend)
    assert response.json() == {"detail": {"reason": "passkey_failed"}}  # no detail
    assert response.status_code == 400
    assert cleared(response, REAUTH_COOKIE)
    assert await failed(penv, category) == 1
    assert (await session(penv, user)).reauth_at is None


async def test_a_replayed_finish_is_refused(penv: Env) -> None:
    client, device = await ready(penv)
    begun = await client.post(REAUTH_START)
    cookie = client.cookies[REAUTH_COOKIE]
    assertion = device.get(begun.json(), None)
    assert (await finish(client, assertion)).status_code == 200
    client.cookies[REAUTH_COOKIE] = cookie
    response = await finish(client, assertion)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failed(penv, "challenge") == 1


async def test_a_counter_regression_refuses_records_mails_and_keeps_the_passkey(
    penv: Env, smtp: SmtpFake
) -> None:
    client, device = await ready(penv)
    user = await user_id_of(penv)
    await penv.run("UPDATE webauthn_credentials SET sign_count = 5")
    await stale(penv, user)
    device.sign_count = 3
    response = await reauth(client, device)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failed(penv, "counter") == 1
    regressed = "SELECT count(*) FROM auth_events WHERE kind = 'passkey_counter_regressed'"
    assert await count(penv, regressed) == 1
    assert await count(penv, "SELECT count(*) FROM webauthn_credentials") == 1
    assert (await session(penv, user)).reauth_at is None
    subjects = [m.subject for m in await mails(smtp, EMAIL, 2)]
    assert REFUSED_MAIL in subjects


async def test_a_passkey_reauth_on_a_new_network_restores_the_guarded_actions(penv: Env) -> None:
    """#632: a move clears the times and flags the session; a passkey re-auth from the new network
    gives the guarded actions back and clears ``location_changed_at`` (section 13)."""
    client, device = await ready(penv)
    user = await user_id_of(penv)
    client.address = MOBILE
    moved = await start(client)  # register start needs recent authentication
    assert (moved.status_code, reason(moved)) == (403, "reauth_required")
    assert (await session(penv, user)).location_changed_at is not None
    assert (await reauth(client, device)).status_code == 200
    assert (await session(penv, user)).location_changed_at is None
    assert (await start(client)).status_code == 200
    [listed] = (await client.get("/api/auth/sessions")).json()
    assert listed["location_changed_at"] is None


async def test_failures_count_in_the_password_reauth_buckets_and_the_429_clears_the_flow(
    penv: Env,
) -> None:
    client, device = await ready(penv)
    await penv.run("UPDATE webauthn_credentials SET sign_count = 100")  # every finish fails
    for _ in range(10):  # the per account and address bucket of a password re-auth (10 / 15 min)
        begun = await client.post(REAUTH_START)
        response = await finish(client, device.get(begun.json(), None))
        assert (response.status_code, reason(response)) == (400, "passkey_failed")
    begun = await client.post(REAUTH_START)
    response = await finish(client, device.get(begun.json(), None))
    assert (response.status_code, reason(response)) == (429, "rate_limited")
    assert cleared(response, REAUTH_COOKIE)
    password = await client.post("/api/auth/reauth", {"password": PASSWORD})
    assert password.status_code == 429  # the same buckets as the password re-auth
