"""The revert link of an email change (#1265, ADR 0003 amendment of 2026-11-19).

The notice to the old address carries a 7-day ``revert_email`` token; ``POST
/api/auth/email/revert`` (public) restores the address and locks the account down in one
transaction, and mails a password-reset link to the restored address.
"""

import hashlib
import logging
from datetime import timedelta
from typing import Any

import httpx
import pytest

from authtools import Env, session_cookie
from flows import PASSWORD, Browser, only_user_id, reason, signed_in, user_with_password
from passkeytools import plant
from smtpfake import SmtpFake

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #1012

OLD = "owner@example.com"
NEW = "attacker@example.net"
REVERT = "/api/auth/email/revert"
NEW_PASSWORD = "a brand new and long password"
UNUSED = "SELECT count(*) FROM auth_tokens WHERE purpose = 'revert_email' AND used_at IS NULL"


async def changed(env: Env, smtp: SmtpFake, old: str = OLD, new: str = NEW) -> Browser:
    """``old`` signs in and moves the account to ``new``; returns that (the changer's) browser."""
    browser = Browser(env)
    if await env.scalar("SELECT count(*) FROM users WHERE email = :e", e=old) == 0:
        await user_with_password(env, old)
    assert (await browser.sign_in(old)).status_code == 200
    assert (await browser.post("/api/auth/email/change", {"new_email": new})).status_code == 202
    token = [m for m in smtp.to(new) if "/email-change#token=" in m.text][-1].token()
    assert (await browser.post("/api/auth/email/confirm", {"token": token})).status_code == 200
    return browser


def revert_token(smtp: SmtpFake, old: str = OLD) -> str:
    return [m for m in smtp.to(old) if "/email-revert#token=" in m.text][-1].token()


async def revert(token: str, env: Env, browser: Browser | None = None) -> httpx.Response:
    return await (browser or Browser(env)).post(REVERT, {"token": token})


async def address(env: Env) -> Any:
    return await env.scalar(
        "SELECT email FROM users WHERE email_normalized IN (:a, :b)", a=OLD, b=NEW
    )


# --- issuing ---------------------------------------------------------------------------------


async def test_confirming_issues_a_seven_day_revert_token_linked_from_the_notice(
    menv: Env, smtp: SmtpFake
) -> None:
    await changed(menv, smtp)
    [notice] = smtp.to(OLD)
    assert "/en/email-revert#token=" in notice.text
    assert "/en/email-revert#token=" in notice.html
    token = notice.token()
    row = await menv.row(
        "SELECT t.user_id = u.id AS mine, t.new_email, t.email_at_issue, t.token_hash, "
        "t.used_at, t.expires_at - t.created_at AS lifetime FROM auth_tokens t, users u "
        "WHERE t.purpose = 'revert_email'"
    )
    assert row.mine
    assert row.used_at is None
    assert (row.new_email, row.email_at_issue) == (OLD, NEW)  # restore OLD while it is NEW
    assert row.token_hash == hashlib.sha256(token.encode()).digest()  # only the SHA-256
    assert timedelta(days=7) - timedelta(seconds=5) < row.lifetime <= timedelta(days=7)
    assert await menv.scalar("SELECT count(*) FROM auth_tokens") == 1


# --- a valid revert --------------------------------------------------------------------------


async def test_a_revert_restores_the_address_and_locks_every_way_in(
    menv: Env, smtp: SmtpFake
) -> None:
    attacker = await changed(menv, smtp)
    user_id = await only_user_id(menv)
    await menv.login(user_id)  # another session
    await menv.run(
        "INSERT INTO identities (user_id, provider, subject, email_at_link) "
        "VALUES (:u, 'github', 'planted', :e)",
        u=user_id,
        e=NEW,
    )
    await menv.run(
        "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, confirmed_at) "
        "VALUES (:u, 'cipher', 'nonce', 'app-v1', now())",
        u=user_id,
    )
    await menv.run(
        "INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (:u, 'h')", u=user_id
    )
    await menv.run("INSERT INTO known_devices (user_id, device_hash) VALUES (:u, 'd')", u=user_id)
    await plant(menv, user_id)

    done = await revert(revert_token(smtp), menv)

    assert (done.status_code, done.json()) == (200, {"status": "reverted"})
    user = await menv.row("SELECT email, email_normalized, password_hash FROM users")
    assert (user.email, user.email_normalized, user.password_hash) == (OLD, OLD, None)
    for table in (
        "sessions",
        "identities",
        "mfa_totp",
        "mfa_recovery_codes",
        "webauthn_credentials",
        "known_devices",
    ):
        assert await menv.scalar(f"SELECT count(*) FROM {table}") == 0, table  # noqa: S608
    # Every earlier token went; the one left is the fresh reset of the mail below.
    assert await menv.scalar("SELECT array_agg(purpose) FROM auth_tokens") == ["reset_password"]
    event = await menv.row("SELECT kind, ip, detail FROM auth_events WHERE kind = 'email_reverted'")
    assert (str(event.ip), event.detail) == (
        "127.0.0.1",  # the test client's address
        {"methods_removed": 1, "passkeys_removed": 1},
    )
    # The changer's session is gone and their password no longer signs in.
    assert (await attacker.get("/api/auth/session")).json()["authenticated"] is False
    assert (await Browser(menv).sign_in(NEW)).status_code != 200
    assert (await Browser(menv).sign_in(OLD)).status_code != 200  # the password was cleared


async def test_the_restored_address_gets_a_reset_link_that_recovers_the_account(
    menv: Env, smtp: SmtpFake
) -> None:
    await changed(menv, smtp)
    assert (await revert(revert_token(smtp), menv)).status_code == 200
    [mail] = [m for m in smtp.to(OLD) if "/reset#token=" in m.text]
    assert mail.subject == "Your classbeat email address was restored"
    assert "/en/reset#token=" in mail.html
    assert [m for m in smtp.to(NEW) if "restored" in m.subject] == []  # never to the new one
    reset = await Browser(menv).post(
        "/api/auth/password/reset/complete",
        {"token": mail.token(), "password": NEW_PASSWORD, "keep_methods": []},
    )
    assert reset.status_code == 200
    owner = Browser(menv)
    assert (await owner.sign_in(OLD, NEW_PASSWORD)).status_code == 200
    events = (await owner.get("/api/auth/events")).json()["events"]
    assert "email_reverted" in [e["kind"] for e in events]  # the activity list shows it


async def test_an_expired_reset_link_can_be_asked_for_again(menv: Env, smtp: SmtpFake) -> None:
    await changed(menv, smtp)
    assert (await revert(revert_token(smtp), menv)).status_code == 200
    await menv.run("DELETE FROM auth_tokens")
    asked = await Browser(menv).post("/api/auth/password/reset/request", {"email": OLD})
    assert asked.status_code == 202
    assert [m for m in smtp.to(OLD) if "/reset#token=" in m.text][-1].subject == (
        "Reset your classbeat password"
    )


async def test_a_signed_in_other_user_reverts_only_the_tokens_account(
    menv: Env, smtp: SmtpFake
) -> None:
    await changed(menv, smtp)
    other = await signed_in(menv, "other@example.com")
    assert (await revert(revert_token(smtp), menv, other)).status_code == 200
    assert await menv.scalar("SELECT email FROM users WHERE email = :e", e=OLD) == OLD
    assert (await other.get("/api/auth/session")).json()["authenticated"] is True
    assert await menv.scalar("SELECT count(*) FROM sessions") == 1  # only the other user's
    assert (
        await menv.scalar(
            "SELECT password_hash IS NOT NULL FROM users WHERE email = :e", e="other@example.com"
        )
        is True
    )


# --- refusals ---------------------------------------------------------------------------------


async def test_unknown_used_expired_and_stale_tokens_get_one_answer(
    menv: Env, smtp: SmtpFake
) -> None:
    await changed(menv, smtp)
    token = revert_token(smtp)
    answers = [await revert("no-such-token", menv)]
    await menv.run("UPDATE auth_tokens SET expires_at = now() - interval '1 second'")
    answers.append(await revert(token, menv))  # expired
    await menv.run("UPDATE auth_tokens SET expires_at = now() + interval '1 day'")
    assert (await revert(token, menv)).status_code == 200
    answers.append(await revert(token, menv))  # used
    assert {(a.status_code, a.text) for a in answers} == {
        (400, '{"detail":{"reason":"invalid_token"}}')
    }


async def test_a_token_is_stale_once_the_address_moved_on(menv: Env, smtp: SmtpFake) -> None:
    await changed(menv, smtp)
    first = revert_token(smtp)
    await changed(menv, smtp, NEW, "third@example.org")  # NEW -> third: OLD's token is stale
    stale = await revert(first, menv)
    assert (stale.status_code, reason(stale)) == (400, "invalid_token")
    assert await menv.scalar("SELECT email FROM users") == "third@example.org"
    # The second change deleted it, so third -> NEW cannot bring it back either.
    await changed(menv, smtp, "third@example.org", NEW)
    assert (await revert(first, menv)).status_code == 400
    assert await menv.scalar("SELECT email FROM users") == NEW


async def test_a_stale_token_found_under_the_lock_is_refused_and_left_unused(
    menv: Env, smtp: SmtpFake
) -> None:
    await changed(menv, smtp)
    await menv.run(
        "UPDATE users SET email = 'moved@example.org', email_normalized = 'moved@example.org'"
    )
    stale = await revert(revert_token(smtp), menv)
    assert (stale.status_code, reason(stale)) == (400, "invalid_token")
    assert await menv.scalar(UNUSED) == 1  # rolled back with the refusal
    assert await menv.scalar("SELECT count(*) FROM sessions") == 1


async def test_tokens_of_other_purposes_do_not_revert(menv: Env, smtp: SmtpFake) -> None:
    browser = await signed_in(menv, OLD)
    await browser.post("/api/auth/email/change", {"new_email": NEW})
    change_token = smtp.to(NEW)[0].token()
    await Browser(menv).post("/api/auth/password/reset/request", {"email": OLD})
    reset_token = next(m for m in smtp.to(OLD) if "/reset#token=" in m.text).token()
    for token in (change_token, reset_token):
        refused = await revert(token, menv)
        assert (refused.status_code, reason(refused)) == (400, "invalid_token")
    assert await menv.scalar("SELECT count(*) FROM auth_tokens WHERE used_at IS NULL") == 2
    assert await menv.scalar("SELECT email FROM users") == OLD


async def test_the_old_address_taken_meanwhile_is_409_and_keeps_the_token(
    menv: Env, smtp: SmtpFake, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await changed(menv, smtp)
    await menv.user(OLD.upper())
    refused = await revert(revert_token(smtp), menv)
    assert (refused.status_code, reason(refused)) == (409, "email_taken")
    assert await menv.scalar("SELECT email FROM users WHERE email = :e", e=NEW) == NEW
    assert await menv.scalar(UNUSED) == 1
    assert await menv.scalar("SELECT count(*) FROM sessions") == 1  # nothing else happened
    assert OLD not in caplog.text
    assert OLD.upper() not in caplog.text


async def test_a_get_does_nothing(menv: Env, smtp: SmtpFake) -> None:
    await changed(menv, smtp)
    token = revert_token(smtp)
    response = await Browser(menv).get(f"{REVERT}?token={token}")
    assert response.status_code != 200
    assert await address(menv) == NEW
    assert await menv.scalar(UNUSED) == 1
    assert (await revert(token, menv)).status_code == 200  # still usable afterwards


async def test_no_session_is_needed_but_csrf_still_is(menv: Env, smtp: SmtpFake) -> None:
    await changed(menv, smtp)
    forged = await menv.client.post(REVERT, json={"token": revert_token(smtp)})
    assert forged.status_code == 403
    assert await address(menv) == NEW
    assert (await revert(revert_token(smtp), menv)).status_code == 200


# --- the revert token outlives what the changer does next ------------------------------------


async def test_the_changers_password_change_and_reset_leave_the_revert_token(
    menv: Env, smtp: SmtpFake
) -> None:
    attacker = await changed(menv, smtp)
    changed_pw = await attacker.post("/api/auth/password/change", {"new_password": NEW_PASSWORD})
    assert changed_pw.status_code == 200
    await Browser(menv).post("/api/auth/password/reset/request", {"email": NEW})
    reset = [m for m in smtp.to(NEW) if "/reset#token=" in m.text][-1].token()
    completed = await Browser(menv).post(
        "/api/auth/password/reset/complete",
        {"token": reset, "password": PASSWORD + " again", "keep_methods": []},
    )
    assert completed.status_code == 200
    assert await menv.scalar(UNUSED) == 1
    assert (await revert(revert_token(smtp), menv)).status_code == 200
    assert await menv.scalar("SELECT email FROM users") == OLD


# --- rate limit -------------------------------------------------------------------------------


async def test_failures_hold_sign_in_ip_slots_and_a_success_gives_its_back(
    menv: Env, smtp: SmtpFake
) -> None:
    from classbeat.auth.ratelimit import SIGN_IN_IP

    digest = menv.app.state.limiter.bucket_hash(SIGN_IN_IP, "127.0.0.1")
    count = "SELECT coalesce(sum(count), 0) FROM rate_limits WHERE bucket_hash = :h"
    await changed(menv, smtp)
    before = await menv.scalar(count, h=digest)
    for _ in range(3):
        assert (await revert("junk", menv)).status_code == 400
    assert await menv.scalar(count, h=digest) == before + 3  # type: ignore[operator]
    assert (await revert(revert_token(smtp), menv)).status_code == 200
    assert await menv.scalar(count, h=digest) == before + 3  # type: ignore[operator]


async def test_the_session_cookie_of_a_reverted_account_no_longer_works(
    menv: Env, smtp: SmtpFake
) -> None:
    await changed(menv, smtp)
    user_id = await only_user_id(menv)
    issued = await menv.login(user_id)
    assert (await revert(revert_token(smtp), menv)).status_code == 200
    response = await menv.client.get("/api/auth/session", headers=session_cookie(issued.token))
    assert response.json()["authenticated"] is False
