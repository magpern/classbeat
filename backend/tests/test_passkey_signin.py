"""Username-less passkey sign-in: start and finish (ADR 0019 sections 5, 6, 10 and 11, #773).

The software authenticator (``webauthnfake``) builds real assertions, so py_webauthn's parsing and
signature checks run unmodified. No network: the netguard is on for every test.
"""

from typing import Any

import pytest
from webauthn.helpers.cose import COSEAlgorithmIdentifier

from authtools import Env, running_env
from classbeat.auth import passkey_assertion
from passkeysignin import (
    SESSIONS,
    SIGNIN_COOKIE,
    SIGNIN_FINISH,
    SIGNIN_START,
    anonymous,
    failures,
    finish_signin,
    sign_in_with_passkey,
    start_signin,
    with_passkey,
)
from passkeytools import REGISTER_COOKIE, cleared, count, reason, signed_in, start
from passkeytools import penv as penv
from smtpfake import SmtpFake
from webauthnfake import RP_ID, SoftAuthenticator, b64url

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

CHALLENGES = "SELECT count(*) FROM webauthn_challenges"


async def test_start_gives_the_same_options_to_everyone_and_a_strict_flow_cookie(
    penv: Env,
) -> None:
    await with_passkey(penv)
    response = await start_signin(anonymous(penv))
    assert response.status_code == 200
    options = response.json()["publicKey"]
    assert options["rpId"] == RP_ID
    assert options["userVerification"] == "required"
    assert options["timeout"] == 300_000
    assert options.get("allowCredentials", []) == []  # discoverable: reveals no account
    assert len(options["challenge"]) == 43  # 32 bytes, base64url
    cookie = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith(f"{SIGNIN_COOKIE}=")
    )
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=300"):
        assert attribute.lower() in cookie.lower()
    stored = await penv.scalar(
        "SELECT user_id IS NULL AND session_id IS NULL FROM webauthn_challenges"
    )
    assert stored is True


async def test_a_passkey_signs_the_browser_in(penv: Env) -> None:
    holder = await with_passkey(penv)
    client = anonymous(penv)
    response = await sign_in_with_passkey(client, holder)
    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(holder.user_id), "email": holder.email, "role": "user"}
    assert cleared(response, SIGNIN_COOKIE)
    state = (await client.get("/api/auth/session")).json()
    assert state["authenticated"] is True
    assert state["session"]["recent_auth"] is True  # a sign-in is a fresh authentication
    assert (state["session"]["mfa"], state["session"]["stepup"]) == (False, False)
    detail = await penv.scalar(
        "SELECT detail FROM auth_events WHERE kind = 'sign_in' AND user_id = :u "
        "AND detail->>'method' = 'passkey'",
        u=holder.user_id,
    )
    assert isinstance(detail, dict)
    assert detail["method"] == "passkey"
    used = await penv.scalar("SELECT last_used_at IS NOT NULL FROM webauthn_credentials")
    assert used is True
    assert await count(penv, CHALLENGES) == 0


async def test_an_eddsa_passkey_signs_in_too(penv: Env) -> None:
    holder = await with_passkey(penv, device=SoftAuthenticator(alg="eddsa"))
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 200


BENT = [
    ({"origin": "https://evil.example"}, "origin"),
    ({"origin": "http://classbeat.test"}, "origin"),
    ({"rp_id": "evil.example"}, "rp_id"),
    ({"uv": False}, "uv"),
    ({"up": False}, "uv"),
    ({"bad_signature": True}, "signature"),
    ({"challenge": b64url(b"x" * 32)}, "challenge"),
    ({"client_type": "webauthn.create"}, "format"),
]


@pytest.mark.parametrize(("bend", "category"), BENT)
async def test_a_bent_assertion_is_refused_and_recorded_as_a_category(
    penv: Env, bend: dict[str, Any], category: str
) -> None:
    holder = await with_passkey(penv)
    client = anonymous(penv)
    response = await sign_in_with_passkey(client, holder, **bend)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert response.json() == {"detail": {"reason": "passkey_failed"}}  # no detail
    assert cleared(response, SIGNIN_COOKIE)
    assert await count(penv, SESSIONS) == 0
    assert await failures(penv, category) == 1


async def test_a_missing_user_handle_is_refused(penv: Env) -> None:
    holder = await with_passkey(penv)
    response = await sign_in_with_passkey(anonymous(penv), holder, handle=None)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "user_handle") == 1
    assert await count(penv, SESSIONS) == 0


async def test_another_users_handle_is_refused(penv: Env) -> None:
    holder = await with_passkey(penv)
    other = await with_passkey(penv, "other@example.com")
    response = await sign_in_with_passkey(anonymous(penv), holder, handle=other.handle)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "user_handle") == 1
    assert await count(penv, SESSIONS) == 0


async def test_a_changed_backup_eligible_flag_is_refused(penv: Env) -> None:
    holder = await with_passkey(penv)
    holder.device.backup_eligible = False
    holder.device.backup_state = False
    response = await sign_in_with_passkey(anonymous(penv), holder)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "backup_flag") == 1


async def test_a_changed_backup_state_is_stored(penv: Env) -> None:
    holder = await with_passkey(penv)
    holder.device.backup_state = False  # eligible still: only the state changed
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 200
    assert await penv.scalar("SELECT backup_state FROM webauthn_credentials") is False


async def test_an_unknown_credential_is_passkey_unknown(penv: Env) -> None:
    holder = await with_passkey(penv)
    stranger = SoftAuthenticator()
    client = anonymous(penv)
    begun = await start_signin(client)
    response = await finish_signin(client, stranger.get(begun.json(), holder.handle))
    assert (response.status_code, reason(response)) == (400, "passkey_unknown")
    assert cleared(response, SIGNIN_COOKIE)


async def test_a_removed_passkey_is_passkey_unknown(penv: Env) -> None:
    holder = await with_passkey(penv)
    await penv.run("DELETE FROM webauthn_credentials")
    response = await sign_in_with_passkey(anonymous(penv), holder)
    assert (response.status_code, reason(response)) == (400, "passkey_unknown")


@pytest.mark.parametrize(
    "credential",
    [
        {},
        {"id": "a", "rawId": "b"},
        {"id": "!!", "rawId": "!!"},
        {"id": b64url(b"x" * 1024), "rawId": b64url(b"x" * 1024)},
    ],
)
async def test_a_malformed_credential_is_the_generic_refusal(
    penv: Env, credential: dict[str, str]
) -> None:
    client = anonymous(penv)
    await start_signin(client)
    response = await finish_signin(client, credential)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "format") == 1


async def test_a_replayed_finish_is_refused(penv: Env) -> None:
    holder = await with_passkey(penv)
    client = anonymous(penv)
    begun = await start_signin(client)
    assertion = holder.device.get(begun.json(), holder.handle)
    cookie = client.cookies[SIGNIN_COOKIE]
    assert (await finish_signin(client, assertion)).status_code == 200
    await client.post("/api/auth/logout")
    client.cookies[SIGNIN_COOKIE] = cookie  # the same flow cookie and assertion again
    again = await finish_signin(client, assertion)
    assert (again.status_code, reason(again)) == (400, "passkey_failed")
    assert await failures(penv, "challenge") == 1
    assert await count(penv, SESSIONS) == 0


async def test_an_expired_challenge_is_refused(penv: Env) -> None:
    holder = await with_passkey(penv)
    client = anonymous(penv)
    begun = await start_signin(client)
    await penv.run("UPDATE webauthn_challenges SET expires_at = now() - interval '1 second'")
    response = await finish_signin(client, holder.device.get(begun.json(), holder.handle))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "challenge") == 1


async def test_another_browsers_challenge_is_refused(penv: Env) -> None:
    """A transplanted assertion (login CSRF, T114): made for one browser's challenge, sent from
    another browser with its own flow cookie. Neither browser is signed in."""
    holder = await with_passkey(penv)
    victim, attacker = anonymous(penv), anonymous(penv)
    planted = await start_signin(attacker)
    await start_signin(victim)
    response = await finish_signin(victim, holder.device.get(planted.json(), holder.handle))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "challenge") == 1
    assert await count(penv, SESSIONS) == 0


async def test_a_finish_without_the_flow_cookie_is_refused(penv: Env) -> None:
    holder = await with_passkey(penv)
    begun = await start_signin(anonymous(penv))
    response = await finish_signin(anonymous(penv), holder.device.get(begun.json(), holder.handle))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, CHALLENGES) == 1  # nothing was taken


async def test_a_register_flow_under_the_signin_cookie_is_refused(penv: Env) -> None:
    """A challenge of another purpose behind the sign-in cookie's hash is the generic failure."""
    holder = await with_passkey(penv)
    client = anonymous(penv)
    begun = await start_signin(client)
    owner = await signed_in(penv, "owner@example.com")
    await start(owner)  # a register row, bound to the owner and their session
    assert REGISTER_COOKIE in owner.cookies
    # Move the register row behind the sign-in cookie, with the sign-in challenge: only the
    # purpose is wrong now.
    flow = await penv.scalar("SELECT flow_hash FROM webauthn_challenges WHERE purpose = 'sign_in'")
    nonce = await penv.scalar("SELECT challenge FROM webauthn_challenges WHERE purpose = 'sign_in'")
    await penv.run("DELETE FROM webauthn_challenges WHERE purpose = 'sign_in'")
    await penv.run(
        "UPDATE webauthn_challenges SET flow_hash = :f, challenge = :c WHERE purpose = 'register'",
        f=flow,
        c=nonce,
    )
    response = await finish_signin(client, holder.device.get(begun.json(), holder.handle))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "challenge") == 1


async def test_a_stored_key_of_another_algorithm_is_refused_at_sign_in(penv: Env) -> None:
    """py_webauthn verifies with whatever algorithm the stored key names; ours checks it is one
    of the three the options offer (ES512 here, planted as if stored by an older rule)."""
    holder = await with_passkey(penv)
    es512 = SoftAuthenticator(alg="es512", credential_id=holder.device.credential_id)
    await penv.run("UPDATE webauthn_credentials SET public_key = :k", k=es512.cose_key())
    holder.device = es512
    response = await sign_in_with_passkey(anonymous(penv), holder)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await failures(penv, "format") == 1


async def test_the_es512_assertion_is_otherwise_valid(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control of the test above: with ES512 added to our list the same sign-in passes, so
    that refusal is our algorithm check (py_webauthn alone would accept it)."""
    monkeypatch.setattr(
        passkey_assertion,
        "ALGORITHMS",
        [*passkey_assertion.ALGORITHMS, COSEAlgorithmIdentifier.ECDSA_SHA_512],
    )
    holder = await with_passkey(penv)
    es512 = SoftAuthenticator(alg="es512", credential_id=holder.device.credential_id)
    await penv.run("UPDATE webauthn_credentials SET public_key = :k", k=es512.cose_key())
    holder.device = es512
    assert (await sign_in_with_passkey(anonymous(penv), holder)).status_code == 200


async def test_a_disabled_user_gets_the_generic_failure(penv: Env) -> None:
    holder = await with_passkey(penv)
    await penv.run("UPDATE users SET disabled_at = now() WHERE id = :u", u=holder.user_id)
    response = await sign_in_with_passkey(anonymous(penv), holder)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert response.json() == {"detail": {"reason": "passkey_failed"}}
    assert await count(penv, SESSIONS) == 0
    assert await failures(penv, "disabled") == 1


async def test_an_admin_still_needs_totp_after_a_passkey_sign_in(penv: Env) -> None:
    holder = await with_passkey(penv, "admin@example.com")
    await penv.run("UPDATE users SET role = 'admin' WHERE id = :u", u=holder.user_id)
    client = anonymous(penv)
    response = await sign_in_with_passkey(client, holder)
    assert response.json()["role"] == "admin"
    session = (await client.get("/api/auth/session")).json()["session"]
    assert (session["mfa"], session["admin_pending_mfa"]) == (False, True)


async def test_admin_emails_grants_the_role_and_still_needs_totp(
    clean_auth: None, app_database_url: str, smtp: SmtpFake
) -> None:
    async with running_env(
        app_database_url,
        passkeys=True,
        webauthn_rp_id=RP_ID,
        trusted_proxies="127.0.0.1",
        admin_emails="boss@example.com",
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
    ) as env:
        holder = await with_passkey(env, "boss@example.com")  # the password sign-in granted it
        await env.run("UPDATE users SET role = 'user'")  # as if listed after that sign-in
        client = anonymous(env)
        response = await sign_in_with_passkey(client, holder)
        assert response.json()["role"] == "admin"
        assert (
            await count(env, "SELECT count(*) FROM auth_events WHERE kind = 'admin_granted'") == 2
        )
        session = (await client.get("/api/auth/session")).json()["session"]
        assert (session["mfa"], session["admin_pending_mfa"]) == (False, True)
        assert await count(env, SESSIONS) == 1  # the grant revoked the rest; this one is new


async def test_a_new_start_replaces_the_browsers_previous_flow(penv: Env) -> None:
    client = anonymous(penv)
    await start_signin(client)
    await start_signin(client)
    assert await count(penv, CHALLENGES) == 1


async def test_with_the_flag_off_both_answer_feature_off(env: Env) -> None:
    client = anonymous(env)
    for path in (SIGNIN_START, SIGNIN_FINISH):
        response = await client.post(path, {"credential": {}})
        assert (response.status_code, reason(response)) == (404, "feature_off")
    assert await count(env, CHALLENGES) == 0
