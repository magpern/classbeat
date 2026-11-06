"""Account notices (ADR 0022 section 4, ME3): one mail per committed change, after the commit,
through the one send path, and nothing in it that is a token, a password or account detail."""

import asyncio
import json
import logging
import re
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Connection, text

from authtools import ORIGIN, Env
from classbeat import auth
from classbeat.emails import live, notices
from classbeat.emails.dispatch import MailPrefs
from flows import PASSWORD, Browser, age_reauth, only_user_id, signed_in, user_with_password
from mfatools import Actor
from smtpfake import SmtpFake
from test_auth_oauth_link import add_identity
from test_auth_reset import EMAIL, NEW, ask, complete

CHANGED = "Your classbeat password was changed"
WELCOME = "Welcome to classbeat"
UNLINKED = "A sign-in method was removed from your classbeat account"
TOTP_OFF = "Two-factor authentication was turned off on your classbeat account"


@pytest.fixture(autouse=True)
def clean_settings(superuser: Connection) -> Any:
    superuser.execute(text("DELETE FROM app_settings"))
    yield
    superuser.execute(text("DELETE FROM app_settings"))


def switch_off(superuser: Connection, *kind_ids: str) -> None:
    superuser.execute(
        text(
            "INSERT INTO app_settings (key, value) "
            "VALUES ('mail_kinds_disabled', CAST(:v AS jsonb))"
        ),
        {"v": json.dumps(list(kind_ids))},
    )


def plain(smtp: SmtpFake, subject: str) -> list[Any]:
    return [m for m in smtp.messages if m.subject == subject]


# ME4: an optional mail's own signed unsubscribe link (it can only turn mail off) is the one token
# link allowed; anything else that carries a token is a leak.
_UNSUBSCRIBE_LINK = re.compile(r"/unsubscribe#token=[\w.-]+(?:&(?:amp;)?all=[\w.-]+)?")


def assert_clean(mail: Any, *secrets: str) -> None:
    """No token link but the unsubscribe one, no password, no secret in either part."""
    for raw in (mail.text, mail.html):
        body = _UNSUBSCRIBE_LINK.sub("/unsubscribe", raw)
        assert "#token=" not in body
        assert "token=" not in body
        assert PASSWORD not in body
        assert NEW not in body
        for secret in secrets:
            assert secret not in body


# ---- password changed ------------------------------------------------------------------------


async def test_a_signed_in_change_mails_once_and_a_refused_one_not_at_all(
    menv: Env, smtp: SmtpFake
) -> None:
    browser = await signed_in(menv)
    weak = await browser.post("/api/auth/password/change", {"new_password": "short"})
    assert weak.status_code == 400
    assert smtp.messages == []  # nothing changed, nothing mailed
    await age_reauth(menv)
    refused = await browser.post("/api/auth/password/change", {"new_password": NEW})
    assert refused.status_code == 403
    assert smtp.messages == []
    assert (await browser.post("/api/auth/reauth", {"password": PASSWORD})).status_code == 200
    assert (
        await browser.post("/api/auth/password/change", {"new_password": NEW})
    ).status_code == 200
    [mail] = smtp.to(EMAIL)
    assert mail.subject == CHANGED
    assert_clean(mail)


async def test_a_completed_reset_mails_once_and_a_bad_token_not_at_all(
    menv: Env, smtp: SmtpFake
) -> None:
    await user_with_password(menv)
    await ask(Browser(menv))
    [request] = smtp.messages
    token = request.token()
    assert (await complete(Browser(menv), "garbage")).status_code == 400
    assert len(smtp.messages) == 1
    assert (await complete(Browser(menv), token)).status_code == 200
    assert (await complete(Browser(menv), token)).status_code == 400  # replayed: no second mail
    [mail] = plain(smtp, CHANGED)
    assert mail.to == EMAIL
    assert_clean(mail, token)


async def test_the_admin_switch_gates_a_notice(
    menv: Env, smtp: SmtpFake, superuser: Connection
) -> None:
    browser = await signed_in(menv)
    switch_off(superuser, "password_changed")
    assert (
        await browser.post("/api/auth/password/change", {"new_password": NEW})
    ).status_code == 200
    assert plain(smtp, CHANGED) == []
    assert (await Browser(menv).sign_in(EMAIL, NEW)).status_code == 200  # the change stood


async def test_a_failing_send_is_logged_by_class_and_never_undoes_the_change(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise ConnectionRefusedError(f"cannot reach the server for {EMAIL}")

    monkeypatch.setattr(live, "send_optional", boom)
    caplog.set_level(logging.WARNING, logger="classbeat.emails.notices")
    browser = await signed_in(menv)
    response = await browser.post("/api/auth/password/change", {"new_password": NEW})
    assert response.status_code == 200
    assert (await Browser(menv).sign_in(EMAIL, NEW)).status_code == 200
    logged = [r.getMessage() for r in caplog.records if "mail not sent" in r.getMessage()]
    assert logged == ["mail not sent (password_changed): ConnectionRefusedError"]


# ---- welcome -----------------------------------------------------------------------------------


async def test_confirming_a_sign_up_sends_the_welcome_without_a_provider(
    menv: Env, smtp: SmtpFake
) -> None:
    from datetime import timedelta

    async with menv.db() as db:
        code = await auth.create_invite(db, timedelta(days=1))
    browser = Browser(menv)
    await browser.post(
        "/api/auth/signup", {"email": "new@example.com", "password": PASSWORD, "invite_code": code}
    )
    [confirmation] = smtp.to("new@example.com")
    token = confirmation.token()
    done = await browser.post("/api/auth/signup/confirm", {"token": token, "password": PASSWORD})
    assert done.status_code == 200, done.text
    [welcome] = plain(smtp, WELCOME)
    assert welcome.to == "new@example.com"
    assert "You can sign in with" not in welcome.text
    assert f"{ORIGIN}/" in welcome.text
    assert_clean(welcome, token)
    again = await browser.post("/api/auth/signup/confirm", {"token": token, "password": PASSWORD})
    assert again.status_code >= 400
    assert len(plain(smtp, WELCOME)) == 1


# ---- sign-in method removed ------------------------------------------------------------------


async def test_unlinking_mails_once_and_a_gone_identity_sends_nothing(
    menv: Env, smtp: SmtpFake
) -> None:
    browser = await signed_in(menv, "me@example.com")
    user = await only_user_id(menv)
    identity = await add_identity(menv, user, "github")
    assert (await browser.request("DELETE", f"/api/auth/identities/{identity}")).status_code == 204
    [mail] = smtp.to("me@example.com")
    assert mail.subject == UNLINKED
    assert "Github" in mail.text  # the same naming as the "method added" mail
    assert_clean(mail, "github-subject")
    again = await browser.request("DELETE", f"/api/auth/identities/{identity}")
    assert again.status_code >= 400
    assert len(smtp.to("me@example.com")) == 1


async def test_the_last_sign_in_method_cannot_be_removed_and_sends_nothing(
    menv: Env, smtp: SmtpFake
) -> None:
    user = await menv.user("me@example.com")  # no password
    only = await add_identity(menv, user, "github")
    issued = await menv.login(user)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=menv.app), base_url=ORIGIN
    ) as client:
        actor = Actor(menv, issued.token, client)
        response = await actor.call("DELETE", f"/api/auth/identities/{only}")
    assert response.status_code == 409
    assert smtp.messages == []


# ---- two-factor turned off ---------------------------------------------------------------------


async def test_turning_totp_off_mails_once(menv: Env, smtp: SmtpFake) -> None:
    user = await menv.user("me@example.com")
    await menv.run(
        "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, confirmed_at) "
        "VALUES (:u, 'x', 'y', 'app-v1', now())",
        u=user,
    )
    issued = await menv.login(user, mfa=True)
    await menv.run("UPDATE sessions SET stepup_at = now()")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=menv.app), base_url=ORIGIN
    ) as client:
        actor = Actor(menv, issued.token, client)
        first = await actor.call("DELETE", "/api/auth/mfa/totp")
        assert first.status_code == 204, first.text
        again = await actor.call("DELETE", "/api/auth/mfa/totp")
    assert again.status_code >= 400
    [mail] = smtp.to("me@example.com")
    assert mail.subject == TOTP_OFF
    assert_clean(mail)


async def test_two_parallel_totp_removals_mail_once(menv: Env, smtp: SmtpFake) -> None:
    user = await menv.user("me@example.com")
    await menv.run(
        "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, confirmed_at) "
        "VALUES (:u, 'x', 'y', 'app-v1', now())",
        u=user,
    )
    issued = await menv.login(user, mfa=True)
    await menv.run("UPDATE sessions SET stepup_at = now()")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=menv.app), base_url=ORIGIN
    ) as client:
        actor = Actor(menv, issued.token, client)
        answers = await asyncio.gather(
            actor.call("DELETE", "/api/auth/mfa/totp"), actor.call("DELETE", "/api/auth/mfa/totp")
        )
    assert 204 in {a.status_code for a in answers}
    assert len(smtp.to("me@example.com")) == 1  # one mail per change


# ---- the preference half of the gate, on the real send path ---------------------------------


def with_prefs(address: str, prefs: MailPrefs) -> notices.Notice:
    base = notices.happened("account_deleted", address)
    return notices.Notice(base.kind_id, address, base.params, prefs)


async def deliver(menv: Env, notice: notices.Notice) -> None:
    await notices.send_notice(menv.app, notice)


async def test_account_deleted_is_sent_with_the_passed_prefs_when_the_lookup_finds_no_user(
    menv: Env, smtp: SmtpFake
) -> None:
    # No account has this address: the real lookup (ME4) finds nobody, as after the deletion.
    address = "old@example.com"
    await deliver(menv, notices.happened("account_deleted", address))
    assert smtp.to(address) == []  # no prefs and no user: dropped, which is why they are passed
    await deliver(menv, with_prefs(address, MailPrefs()))
    [mail] = smtp.to(address)
    assert "deleted" in mail.subject.lower()
    assert_clean(mail)
    # The account is gone: no unsubscribe link (read_prefs carries no user id).
    assert mail.message["List-Unsubscribe"] is None
    assert "/unsubscribe" not in mail.text


async def test_passed_prefs_that_opt_out_drop_the_deleted_mail(menv: Env, smtp: SmtpFake) -> None:
    address = "old@example.com"  # no account: only the passed prefs decide
    for prefs in (
        MailPrefs(unsubscribed_all=True),
        MailPrefs(opted_out=frozenset({"account_activity"})),
    ):
        await deliver(menv, with_prefs(address, prefs))
    assert smtp.to(address) == []


# ---- the notice object ------------------------------------------------------------------------


async def test_a_notice_carries_prefs_only_when_given_and_the_defaults_come_from_one_seam(
    menv: Env,
) -> None:
    async with menv.db() as db:
        assert await notices.read_prefs(db, await menv.user("x@example.com")) == MailPrefs()
        assert await notices.read_prefs(db, uuid4()) == MailPrefs()
    assert notices.happened("totp_removed", "a@example.com").prefs is None
    assert "when" in notices.happened("totp_removed", "a@example.com").params


async def test_read_prefs_reads_the_me4_columns_without_a_user_id(menv: Env) -> None:
    """The deleted-account notice is decided by the stored choice (ME4), and carries no
    unsubscribe link (no ``user_id``) for an account that is about to be gone."""
    user = await menv.user("prefs@example.com")
    await menv.run(
        "UPDATE users SET mail_unsubscribed_all = true, "
        "mail_opted_out = ARRAY['welcome','account_activity'] WHERE id = :i",
        i=user,
    )
    async with menv.db() as db:
        prefs = await notices.read_prefs(db, user)
    assert prefs == MailPrefs(True, frozenset({"welcome", "account_activity"}))
    assert prefs.user_id is None


async def test_deleting_an_opted_out_account_sends_no_deleted_mail(
    menv: Env, smtp: SmtpFake
) -> None:
    user = await menv.user("quiet@example.com")
    await menv.run(
        "UPDATE users SET mail_opted_out = ARRAY['account_activity'] WHERE id = :i", i=user
    )
    async with menv.db() as db:
        prefs = await notices.read_prefs(db, user)
    await menv.run("DELETE FROM users WHERE id = :i", i=user)
    await deliver(menv, with_prefs("quiet@example.com", prefs))
    assert smtp.to("quiet@example.com") == []
