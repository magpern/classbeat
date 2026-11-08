"""Adding a passkey: register start and finish (ADR 0019 sections 4, 6, 10 and 11, #772).

The software authenticator (``webauthnfake``) builds real WebAuthn structures, so py_webauthn's
checks run unmodified. No network: the netguard is on for every test.
"""

import asyncio
from typing import Any

import pytest
from webauthn.helpers.cose import COSEAlgorithmIdentifier

from authtools import Env
from classbeat.auth import passkey_webauthn
from passkeytools import (
    EMAIL,
    FINISH,
    MOBILE,
    REGISTER_COOKIE,
    START,
    Client,
    cleared,
    count,
    finish,
    mails,
    plant,
    reason,
    register,
    second_browser,
    signed_in,
    start,
    user_id_of,
)
from passkeytools import penv as penv
from smtpfake import SmtpFake
from webauthnfake import RP_ID, SoftAuthenticator, b64url, unb64url

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

CREDENTIALS = "SELECT count(*) FROM webauthn_credentials"
CHALLENGES = "SELECT count(*) FROM webauthn_challenges"


# --- the ceremony ------------------------------------------------------------------------------


async def test_start_gives_the_options_of_section_4_and_a_strict_flow_cookie(penv: Env) -> None:
    client = await signed_in(penv)
    response = await start(client)
    assert response.status_code == 200
    options = response.json()["publicKey"]
    assert options["rp"] == {"id": RP_ID, "name": "classbeat"}
    assert options["user"]["name"] == options["user"]["displayName"] == EMAIL
    handle = bytes(await penv.scalar("SELECT webauthn_user_handle FROM users"))  # type: ignore[arg-type]
    assert unb64url(options["user"]["id"]) == handle
    assert len(handle) == 32
    assert len(unb64url(options["challenge"])) == 32
    assert [p["alg"] for p in options["pubKeyCredParams"]] == [-7, -8, -257]
    assert options["authenticatorSelection"] == {
        "residentKey": "required",
        "requireResidentKey": True,
        "userVerification": "required",
    }
    assert options["attestation"] == "none"
    assert options["timeout"] == 300_000
    assert options["excludeCredentials"] == []
    [cookie] = [c for c in response.headers.get_list("set-cookie") if c.startswith(REGISTER_COOKIE)]
    for part in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=300"):
        assert part.lower() in cookie.lower()
    assert "domain" not in cookie.lower()
    row = await penv.row("SELECT purpose, user_id, session_id, challenge FROM webauthn_challenges")
    assert row.purpose == "register"
    assert row.user_id == await user_id_of(penv)
    assert row.session_id == await penv.scalar("SELECT id FROM sessions")
    assert bytes(row.challenge) == unb64url(options["challenge"])


@pytest.mark.parametrize("alg", ["es256", "eddsa"])
async def test_finish_stores_the_passkey_records_it_and_mails_the_user(
    penv: Env, smtp: SmtpFake, alg: str
) -> None:
    client = await signed_in(penv)
    device = SoftAuthenticator(alg=alg, transports=["internal", "hybrid", "carrier-pigeon"])
    response = await register(client, device, label="  MacBook  ")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["label"] == "MacBook"
    assert body["transports"] == ["internal", "hybrid"]  # unknown ones dropped
    assert body["synced"] is True
    assert set(body) == {"id", "label", "transports", "synced", "created_at", "last_used_at"}
    assert cleared(response, REGISTER_COOKIE)
    row = await penv.row("SELECT * FROM webauthn_credentials")
    assert bytes(row.credential_id) == device.credential_id
    assert bytes(row.public_key) == device.cose_key()
    assert (row.user_id, row.sign_count, row.backup_eligible, row.backup_state) == (
        await user_id_of(penv),
        0,
        True,
        True,
    )
    assert await count(penv, CHALLENGES) == 0  # taken
    event = await penv.row("SELECT ip, detail FROM auth_events WHERE kind = 'passkey_added'")
    assert event.detail == {"passkey": body["id"]}  # the row id only: no label, no key
    [mail] = await mails(smtp, EMAIL)
    assert mail.subject == "A passkey was added to your classbeat account"
    assert '"MacBook"' in mail.text
    # The next start excludes it.
    options = (await start(client)).json()["publicKey"]
    assert [unb64url(c["id"]) for c in options["excludeCredentials"]] == [device.credential_id]


async def test_without_a_label_the_label_is_passkey_and_the_date(penv: Env) -> None:
    client = await signed_in(penv)
    response = await register(client, SoftAuthenticator(backup_eligible=False, backup_state=False))
    assert response.status_code == 201
    assert response.json()["label"].startswith("Passkey 20")
    assert response.json()["synced"] is False


@pytest.mark.parametrize("label", ["", "   ", "x" * 65, "line\nbreak", "tab\there"])
async def test_a_bad_label_is_refused_before_the_challenge_is_taken(penv: Env, label: str) -> None:
    client = await signed_in(penv)
    begun = await start(client)
    response = await finish(client, SoftAuthenticator().create(begun.json()), label=label)
    assert response.status_code == 422
    assert await count(penv, CHALLENGES) == 1
    assert label not in response.text or not label.strip()


# --- refusals of section 11 --------------------------------------------------------------------

BENT: dict[str, dict[str, Any]] = {
    "origin": {"origin": "https://classbeat.test.evil.example"},
    "origin port": {"origin": "https://classbeat.test:8443"},
    "origin scheme": {"origin": "http://classbeat.test"},
    "rp id": {"rp_id": "evil.example"},
    "rp id parent": {"rp_id": "test"},
    "challenge": {"challenge": b64url(b"\x00" * 32)},
    "uv unset": {"uv": False},
    "up unset": {"up": False},
    "client data type": {"client_type": "webauthn.get"},
}


@pytest.mark.parametrize("bend", BENT.values(), ids=BENT.keys())
async def test_a_bent_response_is_refused_and_uses_up_the_challenge(
    penv: Env, smtp: SmtpFake, bend: dict[str, Any]
) -> None:
    client = await signed_in(penv)
    begun = await start(client)
    cookie = client.cookies[REGISTER_COOKIE]
    device = SoftAuthenticator()
    refused = await finish(client, device.create(begun.json(), **bend))
    assert (refused.status_code, reason(refused)) == (400, "passkey_failed")
    assert set(refused.json()["detail"]) == {"reason"}  # no library text
    assert cleared(refused, REGISTER_COOKIE)
    assert await count(penv, CREDENTIALS) == 0
    assert await count(penv, "SELECT count(*) FROM auth_events WHERE kind = 'passkey_added'") == 0
    # The challenge is gone: the good response to it is refused now too (single use).
    client.cookies[REGISTER_COOKIE] = cookie
    again = await finish(client, device.create(begun.json()))
    assert (again.status_code, reason(again)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 0
    assert smtp.to(EMAIL) == []


async def test_a_malformed_credential_is_the_same_refusal(penv: Env) -> None:
    client = await signed_in(penv)
    await start(client)
    response = await finish(client, {"id": "x", "response": {"clientDataJSON": "!!"}})
    assert (response.status_code, reason(response)) == (400, "passkey_failed")


async def test_a_replayed_finish_is_refused(penv: Env) -> None:
    client = await signed_in(penv)
    begun = await start(client)
    cookie = client.cookies[REGISTER_COOKIE]
    credential = SoftAuthenticator().create(begun.json())
    assert (await finish(client, credential)).status_code == 201
    client.cookies[REGISTER_COOKIE] = cookie  # the same flow cookie again
    replay = await finish(client, credential)
    assert (replay.status_code, reason(replay)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 1


async def test_parallel_finishes_of_one_flow_store_one_passkey(penv: Env) -> None:
    client = await signed_in(penv)
    begun = await start(client)
    twin = Client(penv)
    twin.cookies = dict(client.cookies)
    one = await asyncio.gather(
        finish(client, SoftAuthenticator().create(begun.json())),
        finish(twin, SoftAuthenticator().create(begun.json())),
    )
    assert sorted(r.status_code for r in one) == [201, 400]
    assert await count(penv, CREDENTIALS) == 1


async def test_an_expired_challenge_is_refused(penv: Env) -> None:
    client = await signed_in(penv)
    begun = await start(client)
    await penv.run("UPDATE webauthn_challenges SET expires_at = now() - interval '1 second'")
    response = await finish(client, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 0


async def test_a_finish_without_the_flow_cookie_is_refused(penv: Env) -> None:
    """Another browser of the same user: it does not hold the cookie, so it finds no flow."""
    client = await signed_in(penv)
    begun = await start(client)
    other = await second_browser(penv)
    response = await finish(other, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 0
    assert await count(penv, CHALLENGES) == 1  # nothing was taken: the starting browser can finish


async def test_another_sessions_flow_is_refused(penv: Env) -> None:
    """The cookie copied into another session of the same user: bound to the starting session."""
    client = await signed_in(penv)
    begun = await start(client)
    other = await second_browser(penv)
    other.cookies[REGISTER_COOKIE] = client.cookies[REGISTER_COOKIE]
    response = await finish(other, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 0
    assert await count(penv, CHALLENGES) == 0  # taken and used up


async def test_another_users_flow_is_refused(penv: Env) -> None:
    victim = await signed_in(penv)
    begun = await start(victim)
    attacker = await signed_in(penv, "attacker@example.com")
    attacker.cookies[REGISTER_COOKIE] = victim.cookies[REGISTER_COOKIE]
    response = await finish(attacker, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 0


async def test_a_new_start_replaces_the_browsers_previous_flow(penv: Env) -> None:
    client = await signed_in(penv)
    first = await start(client)
    second = await start(client)
    assert await count(penv, CHALLENGES) == 1
    stale = await finish(client, SoftAuthenticator().create(first.json()))
    assert (stale.status_code, reason(stale)) == (400, "passkey_failed")
    assert second.status_code == 200


async def test_a_duplicate_credential_id_is_refused_for_the_same_and_another_user(
    penv: Env,
) -> None:
    device = SoftAuthenticator()
    owner = await signed_in(penv)
    assert (await register(owner, device)).status_code == 201
    again = await register(owner, device)
    assert (again.status_code, reason(again)) == (409, "passkey_exists")
    other = await signed_in(penv, "other@example.com")
    theirs = await register(other, SoftAuthenticator(credential_id=device.credential_id))
    assert (theirs.status_code, reason(theirs)) == (409, "passkey_exists")
    assert await count(penv, CREDENTIALS) == 1
    assert cleared(theirs, REGISTER_COOKIE)


async def test_an_eleventh_passkey_is_refused_at_start(penv: Env) -> None:
    client = await signed_in(penv)
    user = await user_id_of(penv)
    for n in range(10):
        await plant(penv, user, f"key {n}")
    response = await start(client)
    assert (response.status_code, reason(response)) == (409, "passkey_limit")
    assert await count(penv, CHALLENGES) == 0


async def test_an_eleventh_passkey_is_refused_at_finish_too(penv: Env) -> None:
    client = await signed_in(penv)
    user = await user_id_of(penv)
    for n in range(9):
        await plant(penv, user, f"key {n}")
    begun = await start(client)
    await plant(penv, user, "the tenth, added meanwhile")
    response = await finish(client, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (409, "passkey_limit")
    assert await count(penv, CREDENTIALS) == 10


# --- recent authentication and step-up, at start and at finish ---------------------------------

AGED_REAUTH = "UPDATE sessions SET reauth_at = now() - interval '11 minutes'"
FRESH_STEPUP = "UPDATE sessions SET stepup_at = now()"
AGED_STEPUP = "UPDATE sessions SET stepup_at = now() - interval '6 minutes'"


async def with_totp(env: Env) -> None:
    """Mark the user's TOTP confirmed, so the step-up applies (the secret is never used here)."""
    await env.run(
        "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, confirmed_at) "
        "SELECT id, '\\x00'::bytea, '\\x00'::bytea, 'app-v1', now() FROM users"
    )


async def test_start_needs_recent_authentication(penv: Env) -> None:
    client = await signed_in(penv)
    await penv.run(AGED_REAUTH)
    response = await start(client)
    assert (response.status_code, reason(response)) == (403, "reauth_required")
    assert await count(penv, CHALLENGES) == 0
    anonymous = await start(Client(penv))
    assert anonymous.status_code == 401


async def test_finish_checks_recent_authentication_again(penv: Env) -> None:
    client = await signed_in(penv)
    begun = await start(client)
    await penv.run(AGED_REAUTH)
    response = await finish(client, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (403, "reauth_required")
    assert await count(penv, CREDENTIALS) == 0


async def test_a_totp_user_needs_the_step_up_at_start_and_at_finish(penv: Env) -> None:
    client = await signed_in(penv)
    await with_totp(penv)
    refused = await start(client)
    assert (refused.status_code, reason(refused)) == (403, "stepup_required")
    await penv.run(FRESH_STEPUP)
    begun = await start(client)
    assert begun.status_code == 200
    await penv.run(AGED_STEPUP)
    late = await finish(client, SoftAuthenticator().create(begun.json()))
    assert (late.status_code, reason(late)) == (403, "stepup_required")
    assert await count(penv, CREDENTIALS) == 0
    await penv.run(FRESH_STEPUP)  # the challenge was not taken: the finish can run now
    done = await finish(client, SoftAuthenticator().create(begun.json()))
    assert done.status_code == 201


async def test_a_network_move_between_start_and_finish_fails_the_finish(penv: Env) -> None:
    """#632 clears the session's recent authentication on a move (ADR 0019 section 13)."""
    client = await signed_in(penv)
    begun = await start(client)
    client.address = MOBILE
    response = await finish(client, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (403, "reauth_required")
    assert await count(penv, CREDENTIALS) == 0


# --- the user handle ---------------------------------------------------------------------------


async def test_two_parallel_first_starts_end_with_one_user_handle(penv: Env) -> None:
    one = await signed_in(penv)
    two = await second_browser(penv)
    answers = await asyncio.gather(start(one), start(two))
    assert [a.status_code for a in answers] == [200, 200]
    handles = {a.json()["publicKey"]["user"]["id"] for a in answers}
    assert len(handles) == 1
    stored = await penv.scalar("SELECT webauthn_user_handle FROM users")
    assert {b64url(bytes(stored))} == handles  # type: ignore[arg-type]
    # And it is kept: a later start gives the same one.
    later = await start(one)
    assert later.json()["publicKey"]["user"]["id"] in handles


# --- the flag and the limits -------------------------------------------------------------------


async def test_with_the_flag_off_both_answer_feature_off_first(env: Env) -> None:
    for path in (START, FINISH):
        anonymous = await Client(env).post(path, {"credential": {}})
        assert (anonymous.status_code, reason(anonymous)) == (404, "feature_off")
    client = await signed_in(env)
    for path in (START, FINISH):
        response = await client.post(path, {"credential": {}})
        assert (response.status_code, reason(response)) == (404, "feature_off")
    assert await count(env, CHALLENGES) == 0


async def test_register_counts_every_request_ten_a_user_and_hour(penv: Env) -> None:
    client = await signed_in(penv)
    for _ in range(10):
        assert (await start(client)).status_code == 200
    limited = await start(client)
    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    finished = await finish(client, {"id": "x"})
    assert finished.status_code == 429  # start and finish share the buckets


async def test_a_credential_with_another_algorithm_is_refused(penv: Env) -> None:
    """#782 review N1: ES512 (-36) is not one of the three offered (ES256, EdDSA, RS256); the
    verification is given the same list, never the library's longer default."""
    client = await signed_in(penv)
    response = await register(client, SoftAuthenticator(alg="es512"))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 0


async def test_the_es512_credential_is_otherwise_valid(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control of the test above: with ES512 added to the list the same credential is stored,
    so that refusal is the algorithm's and nothing else's."""
    monkeypatch.setattr(
        passkey_webauthn,
        "ALGORITHMS",
        [*passkey_webauthn.ALGORITHMS, COSEAlgorithmIdentifier.ECDSA_SHA_512],
    )
    client = await signed_in(penv)
    response = await register(client, SoftAuthenticator(alg="es512"))
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("purpose", ["reauth", "sign_in"])
async def test_a_flow_of_another_purpose_under_the_register_cookie_is_refused(
    penv: Env, purpose: str
) -> None:
    """#782 review N2: the row behind the register cookie's hash is of another purpose (same
    challenge, user and session for ``reauth``; no binding for ``sign_in``)."""
    client = await signed_in(penv)
    begun = await start(client)
    unbind = ", user_id = NULL, session_id = NULL" if purpose == "sign_in" else ""
    await penv.run(f"UPDATE webauthn_challenges SET purpose = :p{unbind}", p=purpose)  # noqa: S608
    response = await finish(client, SoftAuthenticator().create(begun.json()))
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert cleared(response, REGISTER_COOKIE)
    assert await count(penv, CREDENTIALS) == 0
    assert await count(penv, CHALLENGES) == 0  # taken: the browser starts again
