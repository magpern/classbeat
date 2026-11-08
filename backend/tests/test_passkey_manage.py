"""List, rename and remove passkeys, and the flag-off answers (ADR 0019 sections 1, 7 and 10,
#772). Also ADR 0003's last-method rule with passkeys present: passkeys never count toward it."""

from uuid import uuid4

import pytest

from authtools import Env
from classbeat import auth
from flows import Browser
from passkeytools import (
    EMAIL,
    count,
    mails,
    plant,
    reason,
    register,
    signed_in,
    user_id_of,
)
from passkeytools import penv as penv
from smtpfake import SmtpFake
from webauthnfake import SoftAuthenticator

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

LIST = "/api/auth/passkeys"
AGED_REAUTH = "UPDATE sessions SET reauth_at = now() - interval '11 minutes'"


def one(passkey: object) -> str:
    return f"{LIST}/{passkey}"


# --- list --------------------------------------------------------------------------------------


async def test_the_list_shows_only_the_users_own_passkeys_and_nothing_secret(penv: Env) -> None:
    client = await signed_in(penv)
    empty = await client.get(LIST)
    assert empty.json() == {"passkeys": [], "enabled": True, "max": 10}
    assert (await register(client, SoftAuthenticator(), label="Phone")).status_code == 201
    other = await signed_in(penv, "other@example.com")
    assert (await register(other, SoftAuthenticator(), label="Theirs")).status_code == 201
    [mine] = (await client.get(LIST)).json()["passkeys"]
    assert set(mine) == {"id", "label", "transports", "synced", "created_at", "last_used_at"}
    assert (mine["label"], mine["synced"], mine["last_used_at"]) == ("Phone", True, None)
    assert [p["label"] for p in (await other.get(LIST)).json()["passkeys"]] == ["Theirs"]
    assert (await Browser(penv).get(LIST)).status_code == 401


async def test_the_list_needs_no_recent_authentication(penv: Env) -> None:
    client = await signed_in(penv)
    await plant(penv, await user_id_of(penv))
    await penv.run(AGED_REAUTH)
    assert len((await client.get(LIST)).json()["passkeys"]) == 1


# --- rename ------------------------------------------------------------------------------------


async def test_rename_sets_the_trimmed_label_and_records_the_id_only(penv: Env) -> None:
    client = await signed_in(penv)
    passkey = await plant(penv, await user_id_of(penv))
    response = await client.request("PATCH", one(passkey), {"label": "  Work key  "})
    assert response.status_code == 200
    assert response.json()["label"] == "Work key"
    assert await penv.scalar("SELECT label FROM webauthn_credentials") == "Work key"
    detail = await penv.scalar("SELECT detail FROM auth_events WHERE kind = 'passkey_renamed'")
    assert detail == {"passkey": str(passkey)}


@pytest.mark.parametrize("label", ["", "  ", "x" * 65, "two\nlines", "\x00"])
async def test_rename_refuses_a_bad_label(penv: Env, label: str) -> None:
    client = await signed_in(penv)
    passkey = await plant(penv, await user_id_of(penv), "Before")
    response = await client.request("PATCH", one(passkey), {"label": label})
    assert response.status_code == 422
    assert await penv.scalar("SELECT label FROM webauthn_credentials") == "Before"


async def test_rename_answers_404_for_an_unknown_a_foreign_and_a_malformed_id(penv: Env) -> None:
    client = await signed_in(penv)
    await signed_in(penv, "other@example.com")
    theirs = await plant(penv, await user_id_of(penv, "other@example.com"), "Theirs")
    for target in (uuid4(), theirs, "not-a-uuid"):
        response = await client.request("PATCH", one(target), {"label": "Mine now"})
        assert response.status_code == 404
    assert await penv.scalar("SELECT label FROM webauthn_credentials") == "Theirs"


async def test_rename_needs_recent_authentication(penv: Env) -> None:
    client = await signed_in(penv)
    passkey = await plant(penv, await user_id_of(penv))
    await penv.run(AGED_REAUTH)
    response = await client.request("PATCH", one(passkey), {"label": "New"})
    assert (response.status_code, reason(response)) == (403, "reauth_required")


# --- remove ------------------------------------------------------------------------------------


async def test_remove_deletes_records_and_mails(penv: Env, smtp: SmtpFake) -> None:
    client = await signed_in(penv)
    passkey = await plant(penv, await user_id_of(penv), "Old phone")
    response = await client.request("DELETE", one(passkey))
    assert response.status_code == 204
    assert await count(penv, "SELECT count(*) FROM webauthn_credentials") == 0
    detail = await penv.scalar("SELECT detail FROM auth_events WHERE kind = 'passkey_removed'")
    assert detail == {"passkey": str(passkey)}
    [mail] = await mails(smtp, EMAIL)
    assert mail.subject == "A passkey was removed from your classbeat account"
    assert '"Old phone"' in mail.text


async def test_remove_answers_404_for_an_unknown_a_foreign_and_a_malformed_id(
    penv: Env, smtp: SmtpFake
) -> None:
    client = await signed_in(penv)
    await signed_in(penv, "other@example.com")
    theirs = await plant(penv, await user_id_of(penv, "other@example.com"))
    for target in (uuid4(), theirs, "not-a-uuid"):
        assert (await client.request("DELETE", one(target))).status_code == 404
    assert await count(penv, "SELECT count(*) FROM webauthn_credentials") == 1
    assert await count(penv, "SELECT count(*) FROM auth_events WHERE kind = 'passkey_removed'") == 0
    assert smtp.to(EMAIL) == []


async def test_remove_needs_recent_authentication(penv: Env) -> None:
    client = await signed_in(penv)
    passkey = await plant(penv, await user_id_of(penv))
    await penv.run(AGED_REAUTH)
    response = await client.request("DELETE", one(passkey))
    assert (response.status_code, reason(response)) == (403, "reauth_required")
    assert await count(penv, "SELECT count(*) FROM webauthn_credentials") == 1


async def test_rename_and_remove_count_thirty_a_user_and_hour(penv: Env) -> None:
    client = await signed_in(penv)
    for _ in range(30):
        assert (await client.request("PATCH", one(uuid4()), {"label": "x"})).status_code == 404
    assert (await client.request("DELETE", one(uuid4()))).status_code == 429


# --- the last-method rule (ADR 0003 section 5, ADR 0019 section 7) -----------------------------


async def provider_only_user(env: Env) -> tuple[Browser, object, object]:
    """A user without a password, with one identity and one passkey, signed in."""
    user = await env.user(EMAIL)
    identity = await env.scalar(
        "INSERT INTO identities (user_id, provider, subject) VALUES (:u, 'google', 's') "
        "RETURNING id",
        u=user,
    )
    passkey = await plant(env, user)
    browser = Browser(env)
    browser.cookies[auth.SESSION_COOKIE] = (await env.login(user)).token
    return browser, identity, passkey


async def test_the_last_identity_cannot_be_unlinked_even_with_passkeys(penv: Env) -> None:
    browser, identity, _ = await provider_only_user(penv)
    response = await browser.request("DELETE", f"/api/auth/identities/{identity}")
    assert (response.status_code, reason(response)) == (409, "last_sign_in_method")
    assert await count(penv, "SELECT count(*) FROM identities") == 1


async def test_removing_a_passkey_is_always_allowed(penv: Env) -> None:
    browser, _, passkey = await provider_only_user(penv)
    assert (await browser.request("DELETE", one(passkey))).status_code == 204
    assert await count(penv, "SELECT count(*) FROM identities") == 1  # the method that stays


# --- the flag off (section 1 and section 7) ----------------------------------------------------


async def test_with_the_flag_off_list_and_remove_still_answer(env: Env) -> None:
    client = await signed_in(env)
    user = await user_id_of(env)
    first, second = await plant(env, user, "One"), await plant(env, user, "Two")
    listed = (await client.get(LIST)).json()
    assert listed["enabled"] is False
    assert [p["label"] for p in listed["passkeys"]] == ["One", "Two"]
    assert (await client.request("DELETE", one(first))).status_code == 204
    assert (await client.request("DELETE", one(uuid4()))).status_code == 404
    assert [p["id"] for p in (await client.get(LIST)).json()["passkeys"]] == [str(second)]


async def test_with_the_flag_off_rename_answers_feature_off_before_the_id(env: Env) -> None:
    client = await signed_in(env)
    mine = await plant(env, await user_id_of(env))
    for target in (mine, uuid4(), "not-a-uuid"):
        response = await client.request("PATCH", one(target), {"label": "New"})
        assert (response.status_code, reason(response)) == (404, "feature_off")
    anonymous = await Browser(env).request("PATCH", one(mine), {"label": "New"})
    assert (anonymous.status_code, reason(anonymous)) == (404, "feature_off")
    assert await env.scalar("SELECT label FROM webauthn_credentials") == "Planted"
