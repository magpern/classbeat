"""No credential material in logs, events or error answers (ADR 0019 section 10, T117, #773).

Every value the passkey ceremonies carry is planted (made by the software authenticator, or bent
into the client data so py_webauthn's own error text would quote it) and then searched for, in
every form a log line could carry it, in the log output at DEBUG, in every ``auth_events`` row and
in every refusal's body: the credential id, the public key, ``clientDataJSON``,
``attestationObject``, ``authenticatorData``, the signature, the user handle, the challenges and
the flow cookies (register, sign-in and re-auth), plus a planted origin, RP ID and passkey label.
The counter-regression path (its event and mail) runs too. No network: the netguard is on.
"""

import base64
import io
import json
from typing import Any

import httpx
import pytest

from authtools import Env
from classbeat.log import configure_logging
from passkeysignin import SIGNIN_COOKIE, anonymous, finish_signin, start_signin, with_passkey
from passkeytools import REGISTER_COOKIE, finish, signed_in, start
from passkeytools import penv as penv
from test_passkey_reauth import REAUTH_COOKIE, REAUTH_START
from test_passkey_reauth import finish as reauth_finish
from webauthnfake import SoftAuthenticator, b64url, unb64url

pytestmark = pytest.mark.usefixtures("frozen_rate_windows", "restore_logging")

PLANTED_ORIGIN = "https://planted-origin-qqqqqqqqqqqq.example"
PLANTED_RP_ID = "planted-rp-id-wwwwwwwwwwww.example"
PLANTED_LABEL = "planted-label-eeeeeeeeeeee"


def forms(value: bytes | str) -> set[str]:
    """The ways a value can show up in text: as given, base64url, hex, ``repr`` of the bytes."""
    if isinstance(value, str):
        return {value}
    found = {b64url(value), value.hex(), repr(value)[2:-1], base64.b64encode(value).decode()}
    return {f for f in found if len(f) >= 12}  # a short form would match by chance


def planted_in(registration: dict[str, Any], assertion: dict[str, Any]) -> dict[str, bytes | str]:
    """The binary fields of both ceremonies, decoded (``forms`` gives every text form back)."""
    response, signed = registration["response"], assertion["response"]
    return {
        "credential id": unb64url(registration["rawId"]),
        "clientDataJSON (create)": unb64url(response["clientDataJSON"]),
        "attestationObject": unb64url(response["attestationObject"]),
        "clientDataJSON (get)": unb64url(signed["clientDataJSON"]),
        "authenticatorData": unb64url(signed["authenticatorData"]),
        "signature": unb64url(signed["signature"]),
        "user handle": unb64url(signed["userHandle"]),
    }


async def collect_events(env: Env) -> str:
    rows = await env.scalar("SELECT coalesce(json_agg(detail)::text, '') FROM auth_events")
    return str(rows)


async def test_no_planted_value_reaches_a_log_an_event_or_a_refusal(penv: Env) -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream)
    device = SoftAuthenticator()
    planted: dict[str, bytes | str] = {"public key": device.cose_key()}
    refusals: list[httpx.Response] = []

    # Registration, with its challenge and flow cookie.
    owner = await signed_in(penv)
    begun = await start(owner)
    planted["register challenge"] = unb64url(begun.json()["publicKey"]["challenge"])
    planted["register cookie"] = owner.cookies[REGISTER_COOKIE]
    registration = device.create(begun.json())
    assert (await finish(owner, registration, label=PLANTED_LABEL)).status_code == 201
    await owner.post("/api/auth/logout")
    holder = await with_passkey(penv, "second@example.com")  # a second, ordinary user

    # A good sign-in.
    client = anonymous(penv)
    options = (await start_signin(client)).json()
    planted["sign-in challenge"] = unb64url(options["publicKey"]["challenge"])
    planted["sign-in cookie"] = client.cookies[SIGNIN_COOKIE]
    handle = await penv.scalar(
        "SELECT webauthn_user_handle FROM users WHERE email = 'user@example.com'"
    )
    assert isinstance(handle, bytes)
    assertion = device.get(options, handle)
    assert (await finish_signin(client, assertion)).status_code == 200
    planted.update(planted_in(registration, assertion))

    # Refusals whose library errors would quote what the client sent.
    device.sign_count = 0
    bends: list[dict[str, Any]] = [
        {"origin": PLANTED_ORIGIN},
        {"rp_id": PLANTED_RP_ID},
        {"bad_signature": True},
        {"uv": False},
    ]
    for bend in bends:
        browser = anonymous(penv)
        started = (await start_signin(browser)).json()
        refusals.append(await finish_signin(browser, device.get(started, handle, **bend)))
    browser = anonymous(penv)
    await start_signin(browser)
    foreign = holder.device.get(options, handle)  # the other user's passkey, this user's handle
    refusals.append(await finish_signin(browser, foreign))
    browser = anonymous(penv)
    await start_signin(browser)
    broken = {**assertion, "response": {**assertion["response"], "authenticatorData": "AAAA"}}
    refusals.append(await finish_signin(browser, broken))

    # A re-auth in the signed-in browser, with its challenge and flow cookie (#784 review N3).
    device.sign_count = 0
    reauth_options = (await client.post(REAUTH_START)).json()
    planted["re-auth challenge"] = unb64url(reauth_options["publicKey"]["challenge"])
    planted["re-auth cookie"] = client.cookies[REAUTH_COOKIE]
    reauthed = device.get(reauth_options, None)
    assert (await reauth_finish(client, reauthed)).status_code == 200
    planted["clientDataJSON (re-auth)"] = unb64url(reauthed["response"]["clientDataJSON"])
    planted["signature (re-auth)"] = unb64url(reauthed["response"]["signature"])

    # A counter regression: its event, failure event and mail (#783 review N4).
    device.sign_count = 7
    browser = anonymous(penv)
    started = (await start_signin(browser)).json()
    assert (await finish_signin(browser, device.get(started, handle))).status_code == 200
    device.sign_count = 3
    browser = anonymous(penv)
    started = (await start_signin(browser)).json()
    refusals.append(await finish_signin(browser, device.get(started, handle)))
    regressed = "SELECT count(*) FROM auth_events WHERE kind = 'passkey_counter_regressed'"
    assert await penv.scalar(regressed) == 1
    planted["origin"], planted["rp id"] = PLANTED_ORIGIN, PLANTED_RP_ID
    planted["label"] = PLANTED_LABEL

    assert [r.status_code for r in refusals] == [400] * len(refusals)
    logs, events = stream.getvalue(), await collect_events(penv)
    assert "classbeat.access" in logs  # the access log did run
    for name, value in planted.items():
        for form in forms(value):
            assert form not in logs, name
            assert form not in events, name
            for refused in refusals:
                assert form not in refused.text, name
    for line in logs.splitlines():
        json.loads(line)
