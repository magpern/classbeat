"""Mail preferences and the signed unsubscribe link (ADR 0022 section 5; ME4 #655)."""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from authtools import ORIGIN, Env
from classbeat.auth import unsubscribe_token as ut
from classbeat.auth.keys import Keys
from classbeat.auth.notifier import MailNotifier
from classbeat.auth.ratelimit import UNSUBSCRIBE_IP
from classbeat.emails import live
from classbeat.emails.registry import REGISTRY, Category, switchable_categories
from mfatools import make_actor as make_actor
from smtpfake import Received, SmtpFake

SECRET = b"s" * 48
KEYS = Keys.from_secret(SECRET)
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
USER = UUID("11111111-2222-3333-4444-555555555555")
ONE_CLICK = "/api/mail/unsubscribe"
CONFIRM = "/api/mail/unsubscribe/confirm"
PREFS = "/api/mail/preferences"


# --- the token (no database) ------------------------------------------------------------------


def test_a_token_names_its_user_and_scope_and_expires_after_90_days() -> None:
    token = ut.issue_unsubscribe_token(KEYS, USER, "class_ready", now=NOW)
    assert ut.verify_unsubscribe_token(KEYS, token, now=NOW) == (USER, "class_ready")
    last = NOW + timedelta(days=90) - timedelta(seconds=1)
    assert ut.verify_unsubscribe_token(KEYS, token, now=last) is not None
    assert ut.verify_unsubscribe_token(KEYS, token, now=NOW + timedelta(days=90)) is None
    assert ut.verify_unsubscribe_token(KEYS, token, now=NOW + timedelta(days=400)) is None


def test_the_switchable_scopes_are_the_registry_categories_and_all() -> None:
    assert set(ut.CATEGORIES) == set(switchable_categories())
    assert set(ut.CATEGORIES) == {c.value for c in Category} - {"essential"}
    assert "essential" not in ut.SCOPES
    assert {*ut.CATEGORIES, ut.UNSUBSCRIBE_ALL} == ut.SCOPES
    assert all(k.category.value in ut.CATEGORIES for k in REGISTRY.values() if not k.essential)


@pytest.mark.parametrize("scope", ["essential", "", "welcome|all", "unknown"])
def test_a_token_cannot_be_issued_for_anything_but_a_switchable_scope(scope: str) -> None:
    with pytest.raises(ValueError, match="not switchable"):
        ut.issue_unsubscribe_token(KEYS, USER, scope, now=NOW)


def test_a_properly_signed_token_with_an_essential_scope_is_still_refused() -> None:
    expires = int((NOW + timedelta(days=1)).timestamp())
    payload = f"v1|{USER}|essential|{expires}".encode()
    forged = f"{ut._b64(payload)}.{ut._b64(ut._mac(KEYS, payload))}"  # pyright: ignore
    assert ut.verify_unsubscribe_token(KEYS, forged, now=NOW) is None


def test_a_tampered_token_never_verifies() -> None:
    token = ut.issue_unsubscribe_token(KEYS, USER, "welcome", now=NOW)
    body, _, signature = token.partition(".")
    other = ut.issue_unsubscribe_token(KEYS, uuid4(), "welcome", now=NOW)
    everything = ut.issue_unsubscribe_token(KEYS, USER, "all", now=NOW)
    bad = [
        f"{body}.{'A' * len(signature)}",  # a wrong signature
        f"{other.partition('.')[0]}.{signature}",  # another user's payload, this signature
        f"{everything.partition('.')[0]}.{signature}",  # a wider scope, this signature
        f"{body[:-2]}xx.{signature}",  # a changed payload
        body,  # no signature
        f"{body}.{signature}.{signature}",  # an extra part
        f"{body}.",  # an empty signature
        "." + signature,
        "",
        "not a token",
        "é.é",
        "A" * 2000,
    ]
    for candidate in bad:
        assert ut.verify_unsubscribe_token(KEYS, candidate, now=NOW) is None, candidate


def test_a_token_of_another_key_or_secret_does_not_verify() -> None:
    token = ut.issue_unsubscribe_token(KEYS, USER, "welcome", now=NOW)
    assert ut.verify_unsubscribe_token(Keys.from_secret(b"t" * 48), token, now=NOW) is None
    # Derived key: the same secret under another purpose's key does not verify either.
    csrf_signed = ut.issue_unsubscribe_token(
        Keys(**{**{f: getattr(KEYS, f) for f in Keys.__slots__}, "mail_unsubscribe": KEYS.csrf}),
        USER,
        "welcome",
        now=NOW,
    )
    assert ut.verify_unsubscribe_token(KEYS, csrf_signed, now=NOW) is None


# --- the routes -------------------------------------------------------------------------------


async def prefs_of(env: Env, user_id: UUID) -> tuple[bool, list[str]]:
    row = await env.row(
        "SELECT mail_unsubscribed_all, mail_opted_out FROM users WHERE id = :i", i=user_id
    )
    return row[0], list(row[1])


def token_for(env: Env, user_id: UUID, scope: str, **kwargs: Any) -> str:
    return ut.issue_unsubscribe_token(env.app.state.keys, user_id, scope, **kwargs)


async def test_get_never_changes_state(env: Env) -> None:
    user = await env.user("get@example.com")
    token = token_for(env, user, "all")
    for path in (f"{ONE_CLICK}?token={token}", CONFIRM, f"{CONFIRM}?token={token}", "/unsubscribe"):
        response = await env.client.get(path)
        assert response.status_code != 200 or "token" not in response.text
    assert (await env.client.get(f"{ONE_CLICK}?token={token}")).status_code == 405
    assert await prefs_of(env, user) == (False, [])


async def test_the_one_click_post_needs_no_origin_cookie_or_csrf_token(env: Env) -> None:
    user = await env.user("one@example.com")
    other = await env.user("other@example.com")
    token = token_for(env, user, "class_ready")
    response = await env.client.post(
        f"{ONE_CLICK}?token={token}",
        content="List-Unsubscribe=One-Click",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    assert await prefs_of(env, user) == (False, ["class_ready"])
    assert await prefs_of(env, other) == (False, [])
    again = await env.client.post(f"{ONE_CLICK}?token={token}")  # idempotent: one entry
    assert again.status_code == 200
    assert await prefs_of(env, user) == (False, ["class_ready"])


async def test_a_category_token_leaves_the_other_choices_and_all_token_sets_all(env: Env) -> None:
    user = await env.user("cat@example.com")
    await env.client.post(f"{ONE_CLICK}?token={token_for(env, user, 'welcome')}")
    await env.client.post(f"{ONE_CLICK}?token={token_for(env, user, 'account_activity')}")
    assert await prefs_of(env, user) == (False, ["welcome", "account_activity"])
    response = await env.client.post(CONFIRM, json={"token": token_for(env, user, "all")})
    assert response.status_code == 200
    assert await prefs_of(env, user) == (True, ["welcome", "account_activity"])


async def test_a_token_only_turns_things_off(env: Env) -> None:
    user = await env.user("off@example.com")
    await env.run("UPDATE users SET mail_opted_out = ARRAY['welcome'] WHERE id = :i", i=user)
    await env.client.post(f"{ONE_CLICK}?token={token_for(env, user, 'class_ready')}")
    assert (await prefs_of(env, user))[1] == ["welcome", "class_ready"]  # nothing was removed
    # There is no route that turns anything on without a session.
    assert (
        await env.client.put(PREFS, json={"unsubscribed_all": False, "opted_out": []})
    ).status_code in (
        401,
        403,
    )


async def test_every_bad_token_is_the_same_400_and_changes_nothing(env: Env) -> None:
    user = await env.user("bad@example.com")
    good = token_for(env, user, "all")
    expired = token_for(env, user, "all", now=NOW - timedelta(days=91))
    tampered = good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")
    answers = set()
    for token in (expired, tampered, "", "junk", "a.b"):
        response = await env.client.post(f"{ONE_CLICK}?token={token}")
        answers.add((response.status_code, response.text))
        confirm = await env.client.post(CONFIRM, json={"token": token})
        answers.add((confirm.status_code, confirm.text))
    assert len(answers) == 1
    ((status, _),) = answers
    assert status == 400
    assert await prefs_of(env, user) == (False, [])


async def test_a_valid_token_for_an_unknown_user_answers_like_a_known_one(env: Env) -> None:
    known = await env.user("known@example.com")
    ghost = token_for(env, uuid4(), "welcome")
    one = await env.client.post(f"{ONE_CLICK}?token={token_for(env, known, 'welcome')}")
    two = await env.client.post(f"{ONE_CLICK}?token={ghost}")
    assert (two.status_code, two.text) == (one.status_code, one.text) == (200, one.text)
    three = await env.client.post(CONFIRM, json={"token": ghost})
    assert (three.status_code, three.text) == (200, one.text)


async def test_the_unsubscribe_posts_are_limited_per_ip_bad_tokens_too(env: Env) -> None:
    for _ in range(UNSUBSCRIBE_IP.max_count):
        assert (await env.client.post(f"{ONE_CLICK}?token=junk")).status_code == 400
    limited = await env.client.post(f"{ONE_CLICK}?token=junk")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert (await env.client.post(CONFIRM, json={"token": "junk"})).status_code == 429


async def test_only_the_two_unsubscribe_paths_skip_the_origin_check(env: Env) -> None:
    user = await env.user("csrf@example.com")
    actor_token = (await env.login(user)).token
    cookie = {"Cookie": f"classbeat_session={actor_token}"}
    body = {"unsubscribed_all": True, "opted_out": []}
    assert (await env.client.put(PREFS, json=body)).status_code == 403
    assert (await env.client.put(f"{PREFS}/", json=body, headers=cookie)).status_code in (403, 404)
    assert (await env.client.post(f"{ONE_CLICK}/extra")).status_code in (403, 404)
    assert await prefs_of(env, user) == (False, [])


async def test_the_token_is_not_logged(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    user = await env.user("log@example.com")
    token = token_for(env, user, "welcome")
    with caplog.at_level(logging.DEBUG):
        await env.client.post(f"{ONE_CLICK}?token={token}")
        await env.client.post(CONFIRM, json={"token": token})
    assert token not in caplog.text
    assert token.partition(".")[2] not in caplog.text


# --- the account's own choice -----------------------------------------------------------------


async def test_the_signed_in_user_reads_and_sets_their_choice_both_ways(
    env: Env, make_actor: Any
) -> None:
    user = await env.user("me@example.com")
    other = await env.user("not-me@example.com")
    actor = await make_actor(user)
    assert (await actor.get(PREFS)).json() == {"unsubscribed_all": False, "opted_out": []}
    response = await actor.call(
        "PUT", PREFS, {"unsubscribed_all": True, "opted_out": ["welcome", "class_ready"]}
    )
    assert response.json() == {"unsubscribed_all": True, "opted_out": ["class_ready", "welcome"]}
    assert await prefs_of(env, user) == (True, ["class_ready", "welcome"])
    assert await prefs_of(env, other) == (False, [])
    back = await actor.call("PUT", PREFS, {"unsubscribed_all": False, "opted_out": []})
    assert back.status_code == 200
    assert await prefs_of(env, user) == (False, [])


@pytest.mark.parametrize(
    "body",
    [
        {"unsubscribed_all": False, "opted_out": ["essential"]},
        {"unsubscribed_all": False, "opted_out": ["nope"]},
        {"unsubscribed_all": False},
        {"opted_out": []},
        {"unsubscribed_all": False, "opted_out": ["welcome"] * 4},
    ],
)
async def test_the_choice_refuses_essential_and_unknown_categories(
    env: Env, make_actor: Any, body: dict[str, Any]
) -> None:
    user = await env.user("refuse@example.com")
    actor = await make_actor(user)
    assert (await actor.call("PUT", PREFS, body)).status_code == 422
    assert await prefs_of(env, user) == (False, [])


async def test_the_preferences_need_a_session(env: Env) -> None:
    assert (await env.client.get(PREFS)).status_code == 401


async def test_the_table_refuses_an_unknown_category(env: Env) -> None:
    user = await env.user("check@example.com")
    with pytest.raises(DBAPIError):
        await env.run("UPDATE users SET mail_opted_out = ARRAY['essential'] WHERE id = :i", i=user)


async def test_the_export_holds_the_mail_preferences(env: Env) -> None:
    from classbeat.auth.export import account_export

    user = await env.user("export@example.com")
    await env.run(
        "UPDATE users SET mail_unsubscribed_all = true, mail_opted_out = ARRAY['welcome'] "
        "WHERE id = :i",
        i=user,
    )
    async with env.db() as db:
        profile = (await account_export(db, user))["profile"]
    assert (profile["mail_unsubscribed_all"], profile["mail_opted_out"]) == (True, ["welcome"])


# --- the send path ----------------------------------------------------------------------------


def links_of(mail: Received) -> tuple[str, str]:
    page = re.search(r"https://\S+/unsubscribe#token=\S+", mail.text)
    assert page, mail.text
    one_click = str(mail.message["List-Unsubscribe"]).strip("<>")
    return page.group(0), one_click


async def notify(env: Env, user: UUID, email: str) -> None:
    await MailNotifier(env.app.state.mailer, env.settings, env.factory).totp_enrolled(user, email)


async def test_an_optional_mail_carries_the_signed_links_and_the_one_click_headers(
    menv: Env, smtp: SmtpFake
) -> None:
    user = await menv.user("opt@example.com")
    await notify(menv, user, "opt@example.com")
    (mail,) = smtp.to("opt@example.com")
    page, one_click = links_of(mail)
    assert mail.message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert one_click.startswith(f"{ORIGIN}{ONE_CLICK}?token=")
    each, everything = re.fullmatch(r".*#token=([\w.-]+)&all=([\w.-]+)", page).groups()  # pyright: ignore
    keys = menv.app.state.keys
    assert ut.verify_unsubscribe_token(keys, each) == (user, "account_activity")
    assert ut.verify_unsubscribe_token(keys, everything) == (user, "all")
    assert one_click.endswith(each)
    assert page in mail.html.replace("&amp;", "&")
    # The header link works as the mail client sends it: a bare POST of path and query.
    parts = urlsplit(one_click)
    done = await menv.client.post(
        f"{parts.path}?{parts.query}", content="List-Unsubscribe=One-Click"
    )
    assert done.status_code == 200
    assert await prefs_of(menv, user) == (False, ["account_activity"])
    await notify(menv, user, "opt@example.com")
    assert len(smtp.to("opt@example.com")) == 1  # the category is off now


async def test_unsubscribing_from_all_stops_every_optional_kind(menv: Env, smtp: SmtpFake) -> None:
    user = await menv.user("all@example.com")
    await menv.client.post(CONFIRM, json={"token": token_for(menv, user, "all")})
    await notify(menv, user, "all@example.com")
    assert smtp.to("all@example.com") == []


async def test_other_categories_and_other_users_still_get_their_mail(
    menv: Env, smtp: SmtpFake
) -> None:
    off = await menv.user("off@example.com")
    on = await menv.user("on@example.com")
    await menv.run("UPDATE users SET mail_opted_out = ARRAY['welcome'] WHERE id = :i", i=off)
    await notify(menv, off, "off@example.com")  # account_activity is not welcome
    await menv.client.post(f"{ONE_CLICK}?token={token_for(menv, on, 'account_activity')}")
    await notify(menv, off, "off@example.com")
    await notify(menv, on, "on@example.com")
    assert len(smtp.to("off@example.com")) == 2
    assert smtp.to("on@example.com") == []


async def test_essential_mail_is_still_sent_and_has_no_unsubscribe(
    menv: Env, smtp: SmtpFake
) -> None:
    user = await menv.user("ess@example.com")
    await menv.run(
        "UPDATE users SET mail_unsubscribed_all = true, "
        "mail_opted_out = ARRAY['welcome','account_activity','class_ready'] WHERE id = :i",
        i=user,
    )
    sent = await live.send_optional(
        menv.factory,
        menv.app.state.mailer,
        menv.settings,
        "email_changed",
        "ess@example.com",
        REGISTRY["email_changed"].sample,
    )
    assert sent
    (mail,) = smtp.to("ess@example.com")
    assert mail.message["List-Unsubscribe"] is None
    assert mail.message["List-Unsubscribe-Post"] is None
    assert "unsubscribe" not in mail.text.lower()
    await notify(menv, user, "ess@example.com")
    assert len(smtp.to("ess@example.com")) == 1


async def test_an_address_with_no_account_gets_no_optional_mail(menv: Env, smtp: SmtpFake) -> None:
    await notify(menv, uuid4(), "nobody@example.com")
    assert smtp.to("nobody@example.com") == []
