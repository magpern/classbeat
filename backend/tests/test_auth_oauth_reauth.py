"""OAuth re-authentication freshness and the refused-callback log line (#522, ADR 0003 section 5
and its 2026-11-03 amendment).

Google ignores ``max_age=0`` and may leave ``auth_time`` out; a fresh ``iat`` then only shows the
token was minted now, not that the user signed in again. Re-authentication therefore needs a
present ``auth_time`` within 5 minutes for every OIDC provider. Every refused callback logs one
WARNING line with provider, purpose, outcome and why, and never a code, state, nonce, token or
email."""

import logging
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from authtools import Env
from classbeat import auth
from classbeat.auth.oauth_outcomes import AUTH_TIME_SKEW, REAUTH_MAX_AGE, _stale_auth_time
from flows import Browser, age_reauth, signed_in
from oauthtools import location, sign_in_with, start
from oidcfake import DROP, FakeProviders

STALE = "/account?oauth=reauth_stale"
LOGGER = "classbeat.auth.oauth_routes"


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def refused(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.getMessage().startswith("oauth callback refused")
    ]


def refused_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and "callback refused" in r.getMessage()]


async def linked(env: Env, fake: FakeProviders, provider: str = "google") -> Browser:
    browser = await signed_in(env, "me@example.com")
    response = await sign_in_with(browser, fake, provider, purpose="link")
    assert location(response) == "/account?oauth=linked"
    await age_reauth(env)
    return browser


async def still_not_recent(env: Env, browser: Browser, old_cookie: str) -> None:
    assert browser.cookies[auth.SESSION_COOKIE] == old_cookie  # not rotated
    assert await env.scalar("SELECT reauth_at < now() - interval '5 minutes' FROM sessions")
    info = (await browser.get("/api/auth/session")).json()["session"]
    assert info["recent_auth"] is False


async def last_reauth_failure(env: Env) -> object:
    return await env.scalar(
        "SELECT detail FROM auth_events WHERE kind = 'reauth_failed' ORDER BY at DESC LIMIT 1"
    )


# ---- the freshness rule, unit -------------------------------------------------------------------


def test_the_freshness_rule_at_its_bounds() -> None:
    now = 1_800_000_000.0
    assert _stale_auth_time(None, now) == "auth_time_missing"
    assert _stale_auth_time(int(now), now) is None
    assert _stale_auth_time(int(now) - REAUTH_MAX_AGE, now) is None
    assert _stale_auth_time(int(now) - REAUTH_MAX_AGE - 1, now) == "auth_time_old"
    assert _stale_auth_time(int(now) + AUTH_TIME_SKEW, now) is None
    assert _stale_auth_time(int(now) + AUTH_TIME_SKEW + 1, now) == "auth_time_future"
    assert (REAUTH_MAX_AGE, AUTH_TIME_SKEW) == (300, 60)  # the ADR's numbers, never widened


# ---- Google without auth_time -------------------------------------------------------------------


async def test_google_reauth_still_asks_for_auth_time_and_sends_no_prompt_login(
    oenv: Env, fake: FakeProviders
) -> None:
    browser = await linked(oenv, fake)
    started = await start(browser, "google", "reauth")
    query = parse_qs(urlsplit(started.json()["authorization_url"]).query)
    assert query["max_age"] == ["0"]
    assert "auth_time" in query["claims"][0]
    # Google documents no prompt=login, and nothing in its answer would show it was honoured.
    assert "prompt" not in query


@pytest.mark.parametrize(
    "iat_age", [0, 5, 3600], ids=["fresh iat", "iat 5 s old", "iat an hour old"]
)
async def test_google_without_auth_time_fails_closed_whatever_the_iat(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture, iat_age: int
) -> None:
    """A fresh iat, a valid nonce, state and PKCE of this very flow are not proof of a new
    sign-in: Google mints such a token silently from any live Google session."""
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    response = await sign_in_with(
        browser, fake, "google", purpose="reauth", auth_time=DROP, iat=int(time.time()) - iat_age
    )
    assert location(response) == STALE
    await still_not_recent(oenv, browser, old)
    assert await last_reauth_failure(oenv) == {"why": "auth_time_missing", "provider": "google"}
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=reauth_stale "
        "why=auth_time_missing"
    ]


async def test_google_with_a_fresh_auth_time_reauthenticates(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    response = await sign_in_with(
        browser, fake, "google", purpose="reauth", auth_time=int(time.time()) - 10
    )
    assert location(response) == "/account?oauth=reauthed"
    assert browser.cookies[auth.SESSION_COOKIE] != old
    assert (await browser.get("/api/auth/session")).json()["session"]["recent_auth"] is True
    assert refused(logs) == []  # an ok outcome logs no warning


@pytest.mark.parametrize(
    ("provider", "auth_time", "why"),
    [
        ("google", -(REAUTH_MAX_AGE + 1), "auth_time_old"),
        ("google", -3600, "auth_time_old"),
        ("google", AUTH_TIME_SKEW + 30, "auth_time_future"),
        ("microsoft", -3600, "auth_time_old"),
        ("microsoft", None, "auth_time_missing"),
    ],
)
async def test_an_auth_time_present_but_not_fresh_fails_closed(
    oenv: Env,
    fake: FakeProviders,
    logs: pytest.LogCaptureFixture,
    provider: str,
    auth_time: int | None,
    why: str,
) -> None:
    browser = await linked(oenv, fake, provider)
    old = browser.cookies[auth.SESSION_COOKIE]
    claim = DROP if auth_time is None else int(time.time()) + auth_time
    response = await sign_in_with(browser, fake, provider, purpose="reauth", auth_time=claim)
    assert location(response) == STALE
    await still_not_recent(oenv, browser, old)
    assert await last_reauth_failure(oenv) == {"why": why, "provider": provider}
    assert refused(logs) == [
        f"oauth callback refused: provider={provider} purpose=reauth outcome=reauth_stale why={why}"
    ]


# ---- binding to this flow and this session ------------------------------------------------------


async def test_an_iat_in_the_future_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    now = int(time.time())
    response = await sign_in_with(
        browser, fake, "google", purpose="reauth", iat=now + 3600, auth_time=now
    )
    assert location(response) == "/account?oauth=invalid"
    await still_not_recent(oenv, browser, old)
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=invalid "
        "why=provider: issued in the future"
    ]


async def test_a_wrong_nonce_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    response = await sign_in_with(
        browser, fake, "google", purpose="reauth", nonce="another-flows-nonce"
    )
    assert location(response) == "/account?oauth=invalid"
    await still_not_recent(oenv, browser, old)
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=invalid "
        "why=provider: nonce mismatch"
    ]


async def test_a_wrong_state_is_refused_before_any_exchange(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    exchanges = fake.token_requests
    response = await sign_in_with(browser, fake, "google", purpose="reauth", state="forged-state")
    assert location(response) == "/account?oauth=invalid"
    assert fake.token_requests == exchanges
    await still_not_recent(oenv, browser, old)
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=invalid why=state_mismatch"
    ]


async def test_a_wrong_pkce_verifier_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    started = await start(browser, "google", "reauth")
    callback = fake.complete(started.json()["authorization_url"])
    code = parse_qs(urlsplit(callback).query)["code"][0]
    fake.codes[code]["challenge"] = "a-challenge-of-another-flow"
    assert location(await browser.get(callback)) == "/account?oauth=invalid"
    await still_not_recent(oenv, browser, old)
    [line] = refused(logs)
    assert line.startswith("oauth callback refused: provider=google purpose=reauth outcome=invalid")


@pytest.mark.parametrize("switch", ["signed_out", "other_user"])
async def test_a_session_that_changed_during_the_flow_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture, switch: str
) -> None:
    browser = await linked(oenv, fake)
    started = await start(browser, "google", "reauth")
    callback = fake.complete(started.json()["authorization_url"], auth_time=int(time.time()))
    exchanges = fake.token_requests
    if switch == "signed_out":
        assert (await browser.post("/api/auth/logout", {})).status_code in (200, 204)
    else:
        await signed_in_as_other(oenv, browser)
    assert location(await browser.get(callback)) == "/account?oauth=session_mismatch"
    assert fake.token_requests == exchanges  # refused before the code is exchanged
    assert await oenv.scalar("SELECT count(*) FROM auth_events WHERE kind = 'reauth'") == 0
    why = "no_session" if switch == "signed_out" else "other_user"
    assert refused(logs) == [
        f"oauth callback refused: provider=google purpose=reauth outcome=session_mismatch why={why}"
    ]


async def signed_in_as_other(env: Env, browser: Browser) -> None:
    other = await signed_in(env, "other@example.com")
    browser.cookies[auth.SESSION_COOKIE] = other.cookies[auth.SESSION_COOKIE]


async def test_a_google_account_linked_to_nobody_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    response = await sign_in_with(
        browser, fake, "google", purpose="reauth", sub="not-mine", auth_time=int(time.time())
    )
    assert location(response) == "/account?oauth=identity_mismatch"
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=identity_mismatch "
        "why=not_linked_to_this_user"
    ]


async def test_a_google_account_linked_to_another_classbeat_user_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    """The owner check, not only the "linked to nobody" one: user B's Google account must not
    re-authenticate user A's session, even with a fresh auth_time."""
    browser = await linked(oenv, fake)  # user A, with Google subject "sub-1"
    other = await signed_in(oenv, "other@example.com")
    linked_b = await sign_in_with(other, fake, "google", purpose="link", sub="theirs")
    assert location(linked_b) == "/account?oauth=linked"
    owner_b = await oenv.scalar(
        "SELECT user_id::text FROM identities WHERE provider = 'google' AND subject = 'theirs'"
    )
    assert owner_b == await oenv.scalar(
        "SELECT id::text FROM users WHERE email = 'other@example.com'"
    )
    events = await oenv.scalar("SELECT count(*) FROM auth_events")
    identities = await oenv.scalar("SELECT count(*) FROM identities")
    old = browser.cookies[auth.SESSION_COOKIE]
    logs.clear()

    response = await sign_in_with(
        browser, fake, "google", purpose="reauth", sub="theirs", auth_time=int(time.time())
    )
    assert location(response) == "/account?oauth=identity_mismatch"
    assert browser.cookies[auth.SESSION_COOKIE] == old  # not rotated
    assert await oenv.scalar(
        "SELECT bool_and(s.reauth_at < now() - interval '5 minutes') FROM sessions s"
        " JOIN users u ON u.id = s.user_id WHERE u.email = 'me@example.com'"
    )
    assert (await browser.get("/api/auth/session")).json()["session"]["recent_auth"] is False
    # Nothing is recorded or changed: no reauth event (no event at all), no identity moved.
    assert await oenv.scalar("SELECT count(*) FROM auth_events") == events
    assert await oenv.scalar("SELECT count(*) FROM auth_events WHERE kind = 'reauth'") == 0
    assert await oenv.scalar("SELECT count(*) FROM identities") == identities
    assert (
        await oenv.scalar(
            "SELECT user_id::text FROM identities WHERE provider = 'google' AND subject = 'theirs'"
        )
        == owner_b
    )
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=identity_mismatch "
        "why=not_linked_to_this_user"
    ]


# ---- the log line for every other refusal -------------------------------------------------------


async def test_every_refused_callback_logs_one_line(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    # No flow cookie (or a replay).
    started = await start(Browser(oenv), "google")
    callback = fake.complete(started.json()["authorization_url"])
    assert location(await Browser(oenv).get(callback)) == "/signin?oauth=invalid"
    # The flow is bound to its provider.
    browser = Browser(oenv)
    started = await start(browser, "google")
    wrong = fake.complete(started.json()["authorization_url"]).replace("/google/", "/microsoft/")
    assert location(await browser.get(wrong)) == "/signin?oauth=invalid"
    # Denied at the provider: a well-formed error is named, anything else is not echoed.
    # A well-formed word that is no OAuth error code is not echoed either (an allowlist).
    cases = (
        ("access_denied", "access_denied"),
        ("<script>Evil", "unrecognized"),
        ("made_up_word", "unrecognized"),
    )
    for error, named in cases:
        browser = Browser(oenv)
        started = await start(browser, "google")
        state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
        query = urlencode({"error": error, "state": state})
        response = await browser.get(f"/oauth/google/callback?{query}")
        assert location(response) == "/signin?oauth=denied"
        assert refused(logs)[-1].endswith(f"why=provider_error={named}")
    # No code.
    browser = Browser(oenv)
    started = await start(browser, "google")
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    response = await browser.get(f"/oauth/google/callback?{urlencode({'state': state})}")
    assert location(response) == "/signin?oauth=denied"
    # A sign-in outcome refused by the table (no invite).
    response = await sign_in_with(Browser(oenv), fake, "google")
    assert location(response) == "/signin?oauth=invite_required"
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=unknown outcome=invalid why=no_flow",
        "oauth callback refused: provider=microsoft purpose=sign_in outcome=invalid "
        "why=provider_mismatch",
        "oauth callback refused: provider=google purpose=sign_in outcome=denied "
        "why=provider_error=access_denied",
        "oauth callback refused: provider=google purpose=sign_in outcome=denied "
        "why=provider_error=unrecognized",
        "oauth callback refused: provider=google purpose=sign_in outcome=denied "
        "why=provider_error=unrecognized",
        "oauth callback refused: provider=google purpose=sign_in outcome=denied why=no_code",
        "oauth callback refused: provider=google purpose=sign_in outcome=invite_required "
        "why=invite_required",
    ]
    assert all(r.levelno == logging.WARNING for r in refused_records(logs))
    assert "Evil" not in logs.text
    assert "made_up_word" not in logs.text


async def test_the_log_line_holds_no_code_state_nonce_token_or_email(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    started = await start(browser, "google", "reauth")
    url = started.json()["authorization_url"]
    query = parse_qs(urlsplit(url).query)
    callback = fake.complete(url, auth_time=DROP, email="planted-mail@example.com")
    code = parse_qs(urlsplit(callback).query)["code"][0]
    id_token = fake.codes[code]["id_token"]
    assert location(await browser.get(callback)) == STALE
    [record] = refused_records(logs)
    assert record.levelno == logging.WARNING
    line = record.getMessage() + " " + repr(record.args) + " " + repr(record.__dict__)
    planted = [
        code,
        query["state"][0],
        query["nonce"][0],
        query["code_challenge"][0],
        id_token,
        fake.secret,
        browser.cookies[auth.SESSION_COOKIE],
        "planted-mail@example.com",
        "me@example.com",
    ]
    for secret in planted:
        assert secret not in line
        assert secret not in logs.text
    assert "callback?" not in logs.text
