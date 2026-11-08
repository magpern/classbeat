"""Passkeys in the password reset and ``admin mfa-reset`` (ADR 0019 section 9, #775).

The reset page lists the user's passkeys (``info``) and ``complete`` keeps only the ones in
``keep_passkeys``, deleting every other passkey of the token's user under the user row lock;
``admin mfa-reset`` deletes them all. Both whatever ``CLASSBEAT_PASSKEYS`` says, so every test
that does not need the ceremony runs with the flag on (``penv``) and off (``menv``). Events hold
counts only. The races against a register, sign-in and re-auth finish are in
``test_passkey_reset_races``.
"""

from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text

from authtools import Env
from classbeat import auth
from flows import Browser
from passkeytools import (
    EMAIL,
    count,
    plant,
    register,
    signed_in,
    user_id_of,
)
from passkeytools import penv as penv
from smtpfake import SmtpFake
from webauthnfake import SoftAuthenticator

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

NEW = "a brand new passphrase"
INFO = "/api/auth/password/reset/info"
COMPLETE = "/api/auth/password/reset/complete"
PASSKEYS = "SELECT count(*) FROM webauthn_credentials WHERE user_id = :u"
BOGUS = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(params=["flag_on", "flag_off"])
def either(request: pytest.FixtureRequest) -> Env:
    """The app with ``CLASSBEAT_PASSKEYS`` on (``penv``) and off (``menv``), both with SMTP."""
    return request.getfixturevalue("penv" if request.param == "flag_on" else "menv")


async def reset_token(env: Env, smtp: SmtpFake, email: str = EMAIL) -> str:
    asked = await Browser(env).post("/api/auth/password/reset/request", {"email": email})
    assert asked.status_code == 202
    return [m for m in smtp.to(email) if "#token=" in m.text][-1].token()


async def info(env: Env, token: str) -> httpx.Response:
    return await Browser(env).post(INFO, {"token": token})


async def complete(env: Env, token: str, **keep: Any) -> httpx.Response:
    body = {"token": token, "password": NEW, "keep_methods": [], **keep}
    return await Browser(env).post(COMPLETE, body)


async def passkey_ids(env: Env, user_id: UUID) -> set[str]:
    async with env.db() as db:
        rows = await db.execute(
            text("SELECT id FROM webauthn_credentials WHERE user_id = :u"), {"u": user_id}
        )
        return {str(value) for value in rows.scalars()}


async def reset_detail(env: Env) -> Any:
    return await env.scalar("SELECT detail FROM auth_events WHERE kind = 'password_reset'")


# --- info --------------------------------------------------------------------------------------


async def test_info_lists_the_users_passkeys_and_nothing_of_their_keys(
    either: Env, smtp: SmtpFake
) -> None:
    user = await either.user(EMAIL)
    own = await plant(either, user, "Laptop")
    await plant(either, await either.user("other@example.com"), "Not yours")
    token = await reset_token(either, smtp)
    response = await info(either, token)
    assert response.status_code == 200
    [listed] = response.json()["passkeys"]
    assert set(listed) == {"id", "label", "created_at", "last_used_at"}
    assert (listed["id"], listed["label"], listed["last_used_at"]) == (str(own), "Laptop", None)
    # Listing does not consume the token.
    assert (await complete(either, token, keep_passkeys=[str(own)])).status_code == 200


async def test_info_lists_no_passkeys_for_a_user_without_one(either: Env, smtp: SmtpFake) -> None:
    await either.user(EMAIL)
    response = await info(either, await reset_token(either, smtp))
    assert response.json() == {"methods": [], "passkeys": []}


# --- complete ----------------------------------------------------------------------------------


async def test_an_attackers_passkey_is_removed_and_a_kept_one_stays(
    either: Env, smtp: SmtpFake
) -> None:
    user = await either.user(EMAIL)
    mine = await plant(either, user, "Mine")
    attackers = await plant(either, user, "Attacker")
    token = await reset_token(either, smtp)
    done = await complete(either, token, keep_passkeys=[str(mine)])
    assert done.status_code == 200, done.text
    assert await passkey_ids(either, user) == {str(mine)}
    assert attackers != mine
    assert await reset_detail(either) == {"methods_removed": 0, "passkeys_removed": 1}


async def test_an_empty_or_missing_keep_list_removes_every_passkey(
    either: Env, smtp: SmtpFake
) -> None:
    """An empty list removes all; so does a page that sends none (the failure mode of section 9:
    the user, who has just set a password, registers again; the reset never stops)."""
    user = await either.user(EMAIL)
    for label in ("One", "Two"):
        await plant(either, user, label)
    assert (await complete(either, await reset_token(either, smtp), keep_passkeys=[])).is_success
    assert await count(either, PASSKEYS, u=user) == 0
    assert await reset_detail(either) == {"methods_removed": 0, "passkeys_removed": 2}

    await plant(either, user, "Three")
    await either.run("DELETE FROM auth_events")
    assert (await complete(either, await reset_token(either, smtp))).status_code == 200
    assert await count(either, PASSKEYS, u=user) == 0
    assert await reset_detail(either) == {"methods_removed": 0, "passkeys_removed": 1}


async def test_foreign_and_unknown_ids_are_ignored_and_the_reset_completes(
    either: Env, smtp: SmtpFake
) -> None:
    user = await either.user(EMAIL)
    other = await either.user("other@example.com")
    own = await plant(either, user, "Own")
    foreign = await plant(either, other, "Foreign")
    token = await reset_token(either, smtp)
    done = await complete(either, token, keep_passkeys=[str(foreign), BOGUS])
    assert (done.status_code, done.json()) == (200, {"status": "ok"})  # names nothing of others
    assert await count(either, PASSKEYS, u=user) == 0  # own was not on the list
    assert await passkey_ids(either, other) == {str(foreign)}  # the other user's stays
    assert own != foreign
    sessions = await count(either, "SELECT count(*) FROM sessions WHERE user_id = :u", u=user)
    assert sessions == 0


async def test_the_event_and_the_mail_hold_counts_only(either: Env, smtp: SmtpFake) -> None:
    user = await either.user(EMAIL)
    planted = [await plant(either, user, f"Secret label {n}") for n in range(3)]
    token = await reset_token(either, smtp)
    assert (await complete(either, token, keep_passkeys=[str(planted[0])])).status_code == 200
    events = await either.scalar(
        "SELECT string_agg(detail::text, ' ') FROM auth_events WHERE user_id = :u", u=user
    )
    assert isinstance(events, str)
    assert '"passkeys_removed": 2' in events
    for passkey in planted:
        assert str(passkey) not in events
    assert "Secret label" not in events
    for mail in smtp.to(EMAIL):
        assert "Secret label" not in mail.text


async def test_a_keep_list_over_the_cap_is_refused_and_keeps_the_token(
    either: Env, smtp: SmtpFake
) -> None:
    user = await either.user(EMAIL)
    await plant(either, user)
    token = await reset_token(either, smtp)
    many = [f"00000000-0000-0000-0000-{n:012d}" for n in range(101)]
    assert (await complete(either, token, keep_passkeys=many)).status_code == 422
    assert await count(either, PASSKEYS, u=user) == 1
    assert (await complete(either, token, keep_passkeys=many[:100])).status_code == 200
    assert await count(either, PASSKEYS, u=user) == 0


async def test_a_passkey_registered_after_info_is_deleted_by_complete(
    penv: Env, smtp: SmtpFake
) -> None:
    """Review point B: the page keeps what it showed. A passkey registered between ``info`` and
    ``complete`` (by someone holding a session) was never shown, cannot be listed, and goes."""
    client = await signed_in(penv)
    assert (await register(client, SoftAuthenticator())).status_code == 201
    user = await user_id_of(penv)
    token = await reset_token(penv, smtp)
    shown = [p["id"] for p in (await info(penv, token)).json()["passkeys"]]
    assert len(shown) == 1
    assert (await register(client, SoftAuthenticator(), label="Planted")).status_code == 201
    assert await count(penv, PASSKEYS, u=user) == 2
    assert (await complete(penv, token, keep_passkeys=shown)).status_code == 200
    assert await passkey_ids(penv, user) == set(shown)
    assert await reset_detail(penv) == {"methods_removed": 0, "passkeys_removed": 1}


# --- admin mfa-reset -----------------------------------------------------------------------------


async def test_mfa_reset_leaves_no_passkey_and_counts_them_in_its_event(either: Env) -> None:
    user = await either.user(EMAIL)
    other = await either.user("other@example.com")
    for label in ("One", "Two"):
        await plant(either, user, label)
    kept = await plant(either, other, "Other's")
    async with either.db() as db:
        await auth.reset_mfa(db, EMAIL)
    assert await count(either, PASSKEYS, u=user) == 0
    assert await passkey_ids(either, other) == {str(kept)}
    detail = await either.scalar(
        "SELECT detail FROM auth_events WHERE kind = 'mfa_reset' AND user_id = :u", u=user
    )
    assert detail == {"source": "cli", "passkeys_removed": 2}


async def test_the_mfa_reset_link_says_what_was_removed(either: Env, smtp: SmtpFake) -> None:
    user = await either.user(EMAIL)
    sender = auth.make_reset_link_sender(either.settings, either.factory, either.app.state.mailer)
    auth.register_reset_link_sender(sender)
    try:
        assert await auth.send_reset_link(user, EMAIL) is True
    finally:
        auth.register_reset_link_sender(None)
    [mail] = smtp.to(EMAIL)
    assert "recovery codes and passkeys" in " ".join(mail.text.split())
    assert "/reset#token=" in mail.text
    assert (await complete(either, mail.token())).status_code == 200


async def test_a_reset_request_mail_does_not_claim_anything_was_removed(
    either: Env, smtp: SmtpFake
) -> None:
    await either.user(EMAIL)
    await reset_token(either, smtp)
    [mail] = smtp.to(EMAIL)
    assert "passkeys" not in mail.text
