"""OAuth re-authentication freshness and the refused-callback log line (#522, #687, ADR 0003
section 5 and its 2026-11-03 and 2026-11-06 amendments).

Google ignores ``max_age=0`` and may leave ``auth_time`` out; a fresh ``iat`` then only shows the
token was minted now, not that the user signed in again. So (owner decision, #687):

- an account **with** a password cannot re-authenticate through Google at all (the start and the
  callback refuse it); it confirms with the password;
- an account **without** one is re-authenticated by Google's answer to a fresh, single-use flow of
  this session (state, PKCE, nonce, its linked Google subject, a verified email, started at most 5
  minutes ago), without a fresh ``auth_time``;
- every other OIDC provider still needs a present ``auth_time`` within 5 minutes (fail closed).

Every refused callback logs one WARNING line with provider, purpose, outcome and why, and never a
code, state, nonce, token or email."""

import logging
import time
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from authtools import Env
from classbeat import auth
from classbeat.auth.oauth_client import ProviderIdentity
from classbeat.auth.oauth_outcomes import (
    AUTH_TIME_SKEW,
    REAUTH_MAX_AGE,
    Request,
    _freshness,
    _stale_auth_time,
)
from classbeat.auth.oauth_store import Flow
from flows import Browser, age_reauth, reason, signed_in
from oauthtools import add_password, drop_password, location, sign_in_with, start
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


async def linked(
    env: Env, fake: FakeProviders, provider: str = "google", *, password: bool = False
) -> Browser:
    """``me@example.com`` with ``provider`` linked and an old ``reauth_at``. Without ``password``
    the account has no password afterwards (a provider-only account, as a Google sign-up)."""
    browser = await signed_in(env, "me@example.com")
    response = await sign_in_with(browser, fake, provider, purpose="link")
    assert location(response) == "/account?oauth=linked"
    if not password:
        await drop_password(env)
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


def _req(
    provider: str, *, age: float | None, auth_time: int | None, verified: bool = True, now: float
) -> Request:
    flow = Flow(provider, "reauth", b"", "nonce", "verifier", None, None, None, age=age)
    identity = ProviderIdentity("sub-1", "me@example.com", verified, auth_time)
    fields: dict[str, Any] = {"flow": flow, "identity": identity, "now": now}
    return cast(Request, type("R", (), fields)())


def test_the_rule_by_provider_and_password() -> None:
    """``_freshness`` alone (#687): the Google exception needs Google, no password, a flow of at
    most 5 minutes (a missing age fails closed) and a verified email; nothing else changed."""
    now = 1_800_000_000.0

    def rule(provider: str, password: bool, **kw: Any) -> tuple[str, str] | None:
        return _freshness(_req(provider, now=now, **kw), has_password=password)

    assert rule("google", False, age=10, auth_time=None) is None
    assert rule("google", False, age=REAUTH_MAX_AGE, auth_time=int(now) - 3600) is None
    assert rule("google", False, age=REAUTH_MAX_AGE + 1, auth_time=None) == (
        "reauth_expired",
        "flow_old",
    )
    assert rule("google", False, age=None, auth_time=int(now)) == ("reauth_expired", "flow_old")
    assert rule("google", False, age=10, auth_time=None, verified=False) == (
        "invalid",
        "email_unverified",
    )
    assert rule("google", False, age=10, auth_time=int(now) + AUTH_TIME_SKEW + 1) == (
        "reauth_stale",
        "auth_time_future",
    )
    assert rule("google", True, age=10, auth_time=int(now)) == (
        "reauth_use_password",
        "has_password",
    )
    for password in (True, False):
        assert rule("microsoft", password, age=10, auth_time=None) == (
            "reauth_stale",
            "auth_time_missing",
        )
        assert rule("microsoft", password, age=10, auth_time=int(now) - 3600) == (
            "reauth_stale",
            "auth_time_old",
        )
        assert rule("microsoft", password, age=10, auth_time=int(now)) is None
        assert rule("github", password, age=10, auth_time=None) is None  # an accepted risk


# ---- Google and an account with a password: refused (#687) -------------------------------------


async def test_google_reauth_cannot_start_for_an_account_with_a_password(
    oenv: Env, fake: FakeProviders
) -> None:
    browser = await linked(oenv, fake, password=True)
    flows = await oenv.scalar("SELECT count(*) FROM oauth_flows")
    response = await start(browser, "google", "reauth")
    assert (response.status_code, reason(response)) == (403, "reauth_use_password")
    assert "authorization_url" not in response.text
    assert await oenv.scalar("SELECT count(*) FROM oauth_flows") == flows  # no flow was created


@pytest.mark.parametrize(
    "auth_time", [DROP, 0], ids=["no auth_time", "a sign-in at Google just now"]
)
async def test_google_reauth_is_refused_at_the_callback_for_an_account_with_a_password(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture, auth_time: object
) -> None:
    """The server, not only the start, enforces it: a password set between the start and the
    callback (or a start that slipped past) still ends in a refusal, even with a fresh
    auth_time, because the rule is "password accounts use the password"."""
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    started = await start(browser, "google", "reauth")
    assert started.status_code == 200
    await add_password(oenv)
    claim = DROP if auth_time is DROP else int(time.time())
    callback = fake.complete(started.json()["authorization_url"], auth_time=claim)
    assert location(await browser.get(callback)) == "/account?oauth=reauth_use_password"
    await still_not_recent(oenv, browser, old)
    assert await last_reauth_failure(oenv) == {"why": "has_password", "provider": "google"}
    assert await oenv.scalar("SELECT count(*) FROM auth_events WHERE kind = 'reauth'") == 0
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=reauth_use_password "
        "why=has_password"
    ]


async def test_other_providers_still_reauthenticate_an_account_with_a_password(
    oenv: Env, fake: FakeProviders
) -> None:
    browser = await linked(oenv, fake, "microsoft", password=True)
    response = await sign_in_with(
        browser, fake, "microsoft", purpose="reauth", auth_time=int(time.time()) - 10
    )
    assert location(response) == "/account?oauth=reauthed"


# ---- Google and an account without a password: no auth_time needed (#687) ------------------------


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
    "auth_time",
    [DROP, -3600, -(REAUTH_MAX_AGE + 1), -10],
    ids=["no auth_time", "an hour old", "just too old", "fresh"],
)
async def test_google_reauthenticates_an_account_without_a_password_whatever_the_auth_time(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture, auth_time: object
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    claim = DROP if auth_time is DROP else int(time.time()) + int(str(auth_time))
    response = await sign_in_with(browser, fake, "google", purpose="reauth", auth_time=claim)
    assert location(response) == "/account?oauth=reauthed"
    assert browser.cookies[auth.SESSION_COOKIE] != old
    assert (await browser.get("/api/auth/session")).json()["session"]["recent_auth"] is True
    assert await oenv.scalar(
        "SELECT detail FROM auth_events WHERE kind = 'reauth' ORDER BY at DESC LIMIT 1"
    ) == {"provider": "google"}
    assert refused(logs) == []  # an ok outcome logs no warning


async def test_google_reauth_of_a_flow_older_than_5_minutes_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    """The flow lives 10 minutes, but Google's answer counts only within 5 of the start."""
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    started = await start(browser, "google", "reauth")
    await oenv.run(
        "UPDATE oauth_flows SET created_at = now() - make_interval(secs => :age)",
        age=REAUTH_MAX_AGE + 5,
    )
    callback = fake.complete(started.json()["authorization_url"], auth_time=DROP)
    assert location(await browser.get(callback)) == "/account?oauth=reauth_expired"
    await still_not_recent(oenv, browser, old)
    assert await last_reauth_failure(oenv) == {"why": "flow_old", "provider": "google"}
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=reauth_expired why=flow_old"
    ]


async def test_google_reauth_just_inside_5_minutes_passes(oenv: Env, fake: FakeProviders) -> None:
    browser = await linked(oenv, fake)
    started = await start(browser, "google", "reauth")
    await oenv.run(
        "UPDATE oauth_flows SET created_at = now() - make_interval(secs => :age)",
        age=REAUTH_MAX_AGE - 30,
    )
    callback = fake.complete(started.json()["authorization_url"], auth_time=DROP)
    assert location(await browser.get(callback)) == "/account?oauth=reauthed"


async def test_google_reauth_without_a_verified_email_is_refused(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    response = await sign_in_with(
        browser, fake, "google", purpose="reauth", auth_time=DROP, email_verified=False
    )
    assert location(response) == "/account?oauth=invalid"
    await still_not_recent(oenv, browser, old)
    assert await last_reauth_failure(oenv) == {"why": "email_unverified", "provider": "google"}
    assert refused(logs) == [
        "oauth callback refused: provider=google purpose=reauth outcome=invalid "
        "why=email_unverified"
    ]


async def test_google_reauth_with_an_auth_time_in_the_future_is_refused(
    oenv: Env, fake: FakeProviders
) -> None:
    browser = await linked(oenv, fake)
    old = browser.cookies[auth.SESSION_COOKIE]
    response = await sign_in_with(
        browser,
        fake,
        "google",
        purpose="reauth",
        auth_time=int(time.time()) + AUTH_TIME_SKEW + 30,
    )
    assert location(response) == STALE
    await still_not_recent(oenv, browser, old)
    assert await last_reauth_failure(oenv) == {"why": "auth_time_future", "provider": "google"}


# ---- every other OIDC provider: auth_time within 5 minutes, fail closed (#522) -----------------


@pytest.mark.parametrize("password", [True, False], ids=["with a password", "without one"])
@pytest.mark.parametrize(
    ("auth_time", "why"),
    [
        (-(REAUTH_MAX_AGE + 1), "auth_time_old"),
        (-3600, "auth_time_old"),
        (AUTH_TIME_SKEW + 30, "auth_time_future"),
        (None, "auth_time_missing"),
    ],
)
async def test_microsoft_without_a_fresh_auth_time_fails_closed(
    oenv: Env,
    fake: FakeProviders,
    logs: pytest.LogCaptureFixture,
    auth_time: int | None,
    why: str,
    password: bool,
) -> None:
    """The Google rule of #687 is Google's alone: an account without a password gets no
    exception for Microsoft."""
    browser = await linked(oenv, fake, "microsoft", password=password)
    old = browser.cookies[auth.SESSION_COOKIE]
    claim = DROP if auth_time is None else int(time.time()) + auth_time
    response = await sign_in_with(browser, fake, "microsoft", purpose="reauth", auth_time=claim)
    assert location(response) == STALE
    await still_not_recent(oenv, browser, old)
    assert await last_reauth_failure(oenv) == {"why": why, "provider": "microsoft"}
    assert refused(logs) == [
        f"oauth callback refused: provider=microsoft purpose=reauth outcome=reauth_stale why={why}"
    ]


async def test_microsoft_without_auth_time_fails_even_for_an_account_that_also_has_google(
    oenv: Env, fake: FakeProviders
) -> None:
    browser = await signed_in(oenv, "me@example.com")
    for provider in ("google", "microsoft"):
        response = await sign_in_with(browser, fake, provider, purpose="link")
        assert location(response) == "/account?oauth=linked"
    await drop_password(oenv)
    await age_reauth(oenv)
    google = await sign_in_with(browser, fake, "google", purpose="reauth", auth_time=DROP)
    assert location(google) == "/account?oauth=reauthed"  # the Google rule applies to Google
    await age_reauth(oenv)
    response = await sign_in_with(browser, fake, "microsoft", purpose="reauth", auth_time=DROP)
    assert location(response) == STALE
    assert await last_reauth_failure(oenv) == {
        "why": "auth_time_missing",
        "provider": "microsoft",
    }


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
    # A sign-in outcome refused by the table (no account, #617).
    response = await sign_in_with(Browser(oenv), fake, "google")
    assert location(response) == "/signin?oauth=no_account&provider=google"
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
        "oauth callback refused: provider=google purpose=sign_in outcome=no_account why=no_account",
    ]
    assert all(r.levelno == logging.WARNING for r in refused_records(logs))
    assert "Evil" not in logs.text
    assert "made_up_word" not in logs.text


async def test_the_log_line_holds_no_code_state_nonce_token_or_email(
    oenv: Env, fake: FakeProviders, logs: pytest.LogCaptureFixture
) -> None:
    browser = await linked(oenv, fake, "microsoft")
    started = await start(browser, "microsoft", "reauth")
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
