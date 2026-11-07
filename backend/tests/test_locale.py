"""The language preference (ADR 0023 sections 1, 2 and 9; ML2 #664)."""

import time
from typing import Any

import pytest

from authtools import ORIGIN, Env
from classbeat import locales
from classbeat.auth.export import account_export
from classbeat.config import Settings
from flows import PASSWORD, Browser, reason
from mfatools import make_actor as make_actor
from oauthtools import location, make_invite, start
from oauthtools import oenv as oenv
from oidcfake import FakeProviders
from smtpfake import SmtpFake
from test_auth_signup import EMAIL, confirm
from test_auth_signup import invite as signup_invite

LOCALE = "/api/account/locale"
OFFERED = ("en", "sv", "da")


@pytest.fixture(autouse=True)
def three_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest lists only English until ML3 ships catalogues; these tests need three."""
    monkeypatch.setattr(locales, "SUPPORTED_LOCALES", OFFERED)


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


# --- the pure parts (no database) -------------------------------------------------------------


def test_the_offered_set_is_enabled_and_supported_with_english_always_on() -> None:
    assert locales.offered_locales(settings()) == OFFERED
    assert locales.offered_locales(settings(locales="sv")) == ("en", "sv")
    assert locales.offered_locales(settings(locales="fr,sv,xx9,")) == ("en", "sv")
    assert settings(locales="").enabled_locales == frozenset({"en"})


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("sv", "sv"),
        ("da, sv;q=0.5", "da"),
        ("sv;q=0.2, da;q=0.9", "da"),
        ("fr, de;q=0.9, da;q=0.1", "da"),
        ("sv;q=0", None),
        ("sv-SE", None),
        ("fr, de", None),
        ("sv;q=banana", None),
        ("", None),
        (None, None),
    ],
)
def test_accept_language_picks_the_best_offered_exact_match(
    header: str | None, expected: str | None
) -> None:
    assert locales.parse_accept_language(header, OFFERED) == expected


def test_a_huge_accept_language_is_bounded() -> None:
    header = ",".join(["xx"] * 3000) + ",sv"
    assert len(header) > 8 * 1024
    started = time.perf_counter()
    for _ in range(200):
        assert locales.parse_accept_language(header, OFFERED) is None
    assert time.perf_counter() - started < 1.0
    # Only the first eight entries count, even when a later one is offered.
    assert locales.parse_accept_language(",".join(["fr"] * 8) + ",sv", OFFERED) is None
    assert locales.parse_accept_language(",".join(["fr"] * 7) + ",sv", OFFERED) == "sv"


def test_the_body_field_wins_then_the_header_then_english() -> None:
    s = settings()
    assert locales.request_locale(s, "da", "sv") == "da"
    assert locales.request_locale(s, "fr", "sv") == "sv"
    assert locales.request_locale(s, 7, "sv") == "sv"
    assert locales.request_locale(s, None, "fr") == "en"
    assert locales.request_locale(s, "s" * 5000, None) == "en"


def test_one_offered_language_stores_null() -> None:
    assert locales.storable_locale(settings(locales="en"), "en") is None
    assert locales.storable_locale(settings(), "sv") == "sv"


# --- the account route ------------------------------------------------------------------------


async def stored(env: Env, email: str) -> object:
    return await env.scalar("SELECT locale FROM users WHERE email = :e", e=email)


async def test_a_new_cli_style_account_has_no_language(env: Env) -> None:
    await env.user("cli@example.com")
    assert await stored(env, "cli@example.com") is None


async def test_the_user_sets_clears_and_reads_their_language(env: Env, make_actor: Any) -> None:
    user = await env.user("me@example.com")
    await env.user("other@example.com")
    actor = await make_actor(user)
    first = (await actor.get("/api/auth/session")).json()
    assert first["user"]["locale"] is None
    assert first["locales"] == list(OFFERED)
    response = await actor.call("PUT", LOCALE, {"locale": "sv"})
    assert (response.status_code, response.json()) == (200, {"locale": "sv"})
    assert (await actor.get("/api/auth/session")).json()["user"]["locale"] == "sv"
    assert await stored(env, "me@example.com") == "sv"
    assert await stored(env, "other@example.com") is None  # nobody else's value moved
    assert (await actor.call("PUT", LOCALE, {"locale": None})).json() == {"locale": None}
    assert await stored(env, "me@example.com") is None


@pytest.mark.parametrize("value", ["fr", "SV", "sv-SE", "", "../x", 5, ["sv"], "x" * 500])
async def test_an_unknown_or_malformed_code_is_refused_and_not_echoed(
    env: Env, make_actor: Any, value: Any
) -> None:
    actor = await make_actor(await env.user("refuse@example.com"))
    response = await actor.call("PUT", LOCALE, {"locale": value})
    assert (response.status_code, reason(response)) == (400, "locale_not_offered")
    assert "x" * 50 not in response.text
    assert "../x" not in response.text
    assert await stored(env, "refuse@example.com") is None


async def test_a_disabled_language_is_refused(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env.settings, "locales", "en,sv")
    actor = await make_actor(await env.user("off@example.com"))
    assert (await actor.call("PUT", LOCALE, {"locale": "da"})).status_code == 400
    assert (await actor.call("PUT", LOCALE, {"locale": "sv"})).status_code == 200
    assert (await actor.get("/api/auth/session")).json()["locales"] == ["en", "sv"]


async def test_with_only_english_enabled_the_session_offers_english_and_refuses_the_rest(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The roadmap done-when for ``CLASSBEAT_LOCALES=en``, on the real session endpoint."""
    monkeypatch.setattr(env.settings, "locales", "en")
    actor = await make_actor(await env.user("en@example.com"))
    assert (await actor.get("/api/auth/session")).json()["locales"] == ["en"]
    assert (await actor.call("PUT", LOCALE, {"locale": "sv"})).status_code == 400
    assert await stored(env, "en@example.com") is None


async def test_the_key_is_required_and_a_session_too(env: Env, make_actor: Any) -> None:
    actor = await make_actor(await env.user("req@example.com"))
    assert (await actor.call("PUT", LOCALE, {})).status_code == 422
    browser = Browser(env)
    response = await browser.request("PUT", LOCALE, {"locale": "sv"})
    assert response.status_code == 401


async def test_the_anonymous_session_lists_the_offered_languages(env: Env) -> None:
    body = (await env.client.get("/api/auth/session")).json()
    assert body["locales"] == list(OFFERED)


async def test_the_export_holds_the_language(env: Env) -> None:
    user = await env.user("export@example.com")
    await env.run("UPDATE users SET locale = 'da' WHERE id = :i", i=user)
    async with env.db() as db:
        assert (await account_export(db, user))["profile"]["locale"] == "da"


async def test_the_table_refuses_a_code_that_is_not_two_lowercase_letters(env: Env) -> None:
    from sqlalchemy.exc import DBAPIError

    user = await env.user("check@example.com")
    for bad in ("SV", "swe", "s", ""):
        with pytest.raises(DBAPIError):
            await env.run("UPDATE users SET locale = :l WHERE id = :i", l=bad, i=user)


# --- the sign-up paths ------------------------------------------------------------------------


async def test_email_sign_up_keeps_the_body_language(menv: Env, smtp: SmtpFake) -> None:
    browser = Browser(menv)
    code = await signup_invite(menv)
    response = await browser.post(
        "/api/auth/signup",
        {"email": EMAIL, "password": PASSWORD, "invite_code": code, "locale": "da"},
    )
    assert response.status_code == 202, response.text
    assert await menv.scalar("SELECT locale FROM pending_signups") == "da"
    assert (await confirm(Browser(menv), smtp.to(EMAIL)[-1].token())).status_code == 200
    assert await stored(menv, EMAIL) == "da"


async def test_email_sign_up_falls_back_to_the_header_then_english(
    menv: Env, smtp: SmtpFake
) -> None:
    browser = Browser(menv)
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": await browser.csrf(),
        "Accept-Language": "fr, sv;q=0.8",
    }
    body = {"email": EMAIL, "password": PASSWORD, "invite_code": await signup_invite(menv)}
    response = await menv.client.post("/api/auth/signup", json=body, headers=headers)
    assert response.status_code == 202, response.text
    assert await menv.scalar("SELECT locale FROM pending_signups") == "sv"


@pytest.mark.parametrize("value", ["fr", {"x": 1}, 7, "x" * 500])
async def test_an_unknown_body_language_is_ignored_not_refused(
    menv: Env, smtp: SmtpFake, value: Any
) -> None:
    body = {
        "email": EMAIL,
        "password": PASSWORD,
        "invite_code": await signup_invite(menv),
        "locale": value,
    }
    response = await Browser(menv).post("/api/auth/signup", body)
    assert response.status_code == 202, response.text
    assert await menv.scalar("SELECT locale FROM pending_signups") == "en"


async def test_one_offered_language_stores_nothing(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(menv.settings, "locales", "en")
    response = await Browser(menv).post(
        "/api/auth/signup",
        {
            "email": EMAIL,
            "password": PASSWORD,
            "invite_code": await signup_invite(menv),
            "locale": "sv",
        },
    )
    assert response.status_code == 202
    assert await menv.scalar("SELECT locale FROM pending_signups") is None


async def test_a_verified_provider_sign_up_keeps_the_language(
    oenv: Env, fake: FakeProviders
) -> None:
    browser = Browser(oenv)
    started = await start(
        browser, "google", "sign_up", invite_code=await make_invite(oenv), locale="sv"
    )
    assert started.status_code == 200, started.text
    assert await oenv.scalar("SELECT locale FROM oauth_flows") == "sv"
    response = await browser.get(fake.complete(started.json()["authorization_url"]))
    assert location(response)
    assert await oenv.scalar("SELECT locale FROM users") == "sv"


async def test_a_hand_off_sign_up_carries_the_language_to_the_account(
    oenv: Env, fake: FakeProviders, smtp: SmtpFake
) -> None:
    browser = Browser(oenv)
    started = await start(
        browser, "microsoft", "sign_up", invite_code=await make_invite(oenv), locale="da"
    )
    callback = fake.complete(started.json()["authorization_url"])
    await browser.get(callback)
    assert await oenv.scalar("SELECT locale FROM pending_signups") == "da"
    await browser.post("/api/auth/oauth/pending/email", {"email": "hand@example.com"})
    assert await oenv.scalar("SELECT locale FROM pending_signups") == "da"
    assert len(smtp.to("hand@example.com")) == 1
    token = smtp.to("hand@example.com")[-1].token()
    done = await browser.post("/api/auth/signup/confirm", {"token": token})
    assert done.status_code == 200, done.text
    assert await oenv.scalar("SELECT locale FROM users") == "da"


async def test_the_hand_off_email_step_replaces_the_row_language_with_the_one_it_names(
    oenv: Env, fake: FakeProviders, smtp: SmtpFake
) -> None:
    browser = Browser(oenv)
    started = await start(
        browser, "microsoft", "sign_up", invite_code=await make_invite(oenv), locale="da"
    )
    await browser.get(fake.complete(started.json()["authorization_url"]))
    assert await oenv.scalar("SELECT locale FROM pending_signups") == "da"
    await browser.post(
        "/api/auth/oauth/pending/email", {"email": "swap@example.com", "locale": "sv"}
    )
    assert await oenv.scalar("SELECT locale FROM pending_signups") == "sv"
    token = smtp.to("swap@example.com")[-1].token()
    done = await browser.post("/api/auth/signup/confirm", {"token": token})
    assert done.status_code == 200, done.text
    assert await oenv.scalar("SELECT locale FROM users") == "sv"
