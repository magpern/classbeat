"""Turnstile on the four anonymous auth routes (ADR 0025 sections 1 to 6), against the database.

With Turnstile on, each route checks the token first after the body's shape: a recording verifier
and a spy session factory show the verifier runs before the route's first database session (the
only earlier write is the limiter's own reservation of the captcha bucket), and a refused token
leaves no ``auth_events`` row, no ``rate_limits`` row outside the captcha bucket, no
``pending_signups`` row, no mail and no cookie. Every refusal looks the same, whether or not the
account exists. Fakes only (``FakeTurnstileVerifier`` and the recorder); netguard stays on.
"""

import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest

from authtools import Env, running_env
from classbeat import auth
from classbeat.auth.captcha import CaptchaAction, CaptchaFailed, CaptchaUnavailable
from classbeat.auth.clientip import bucket_ip
from classbeat.auth.ratelimit import CAPTCHA_IP
from flows import PASSWORD, Browser, reason, user_with_password
from test_turnstile_guard import TURNSTILE

EMAIL = "user@example.com"
CLIENT_IP = "127.0.0.1"  # the ASGI test client's peer; no trusted proxy


class Recorder:
    """A verifier that logs its calls into the shared ``log`` and answers ``outcome``."""

    def __init__(self, log: list[str], outcome: Exception | None = None) -> None:
        self.log = log
        self.outcome = outcome
        self.calls: list[tuple[str, str, str | None]] = []

    async def verify(self, token: str, action: CaptchaAction, remoteip: str | None) -> None:
        self.log.append("verify")
        self.calls.append((token, action, remoteip))
        if self.outcome is not None:
            raise self.outcome


class SpyFactory:
    """Stands in for ``app.state.session_factory`` and logs every session a route opens. The
    limiter keeps the factory it was built with, so its reservations are not logged."""

    def __init__(self, inner: Any, log: list[str]) -> None:
        self.inner = inner
        self.log = log

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.log.append("db")
        return self.inner(*args, **kwargs)


class MailSpy:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)


@pytest.fixture
async def tenv(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    """The app with Turnstile on and the e2e fake verifier (no network)."""
    async with running_env(app_database_url, turnstile_enabled=True, turnstile_fake=True) as env:
        env.app.state.mailer = MailSpy()
        yield env


def body_for(path: str, token: object = None, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {"email": EMAIL}
    if path == "/api/auth/login":
        base["password"] = PASSWORD
    elif path == "/api/auth/signup":
        base.update(password=PASSWORD, invite_code="not-an-invite")
    if token is not None:
        base["captcha_token"] = token
    base.update(extra)
    return base


ROUTES = sorted(TURNSTILE.items())


async def captcha_rows_only(env: Env) -> None:
    expected = env.app.state.limiter.bucket_hash(CAPTCHA_IP, bucket_ip(CLIENT_IP))
    async with env.db() as db:
        from sqlalchemy import text

        hashes = (await db.execute(text("SELECT bucket_hash FROM rate_limits"))).scalars().all()
    assert hashes
    assert all(bytes(h) == expected for h in hashes)


async def nothing_happened(env: Env, response: httpx.Response) -> None:
    assert await env.scalar("SELECT count(*) FROM auth_events") == 0
    assert await env.scalar("SELECT count(*) FROM pending_signups") == 0
    assert await env.scalar("SELECT count(*) FROM sessions") == 0
    await captcha_rows_only(env)
    assert env.app.state.mailer.sent == []
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.parametrize(("route", "action"), ROUTES)
async def test_the_verifier_runs_before_the_first_database_session(
    tenv: Env, route: tuple[str, str], action: str
) -> None:
    await user_with_password(tenv)
    browser = Browser(tenv)
    csrf = await browser.csrf()
    log: list[str] = []
    recorder = Recorder(log)
    tenv.app.state.captcha = recorder
    tenv.app.state.session_factory = SpyFactory(tenv.app.state.session_factory, log)
    response = await browser.post(route[1], body_for(route[1], "tok-ok"), csrf=csrf)
    assert response.status_code < 500, response.text
    assert log[0] == "verify"
    assert "db" in log  # the route went on to its own work
    assert recorder.calls == [("tok-ok", action, CLIENT_IP)]


@pytest.mark.parametrize(("route", "action"), ROUTES)
async def test_a_refused_token_touches_no_account_bucket_mail_or_session(
    tenv: Env, route: tuple[str, str], action: str, caplog: pytest.LogCaptureFixture
) -> None:
    await user_with_password(tenv)
    async with tenv.db() as db:
        code = await auth.create_invite(db, timedelta(days=1))
    browser = Browser(tenv)
    csrf = await browser.csrf()
    log: list[str] = []
    tenv.app.state.captcha = Recorder(log, CaptchaFailed("invalid-input-response"))
    tenv.app.state.session_factory = SpyFactory(tenv.app.state.session_factory, log)
    secret_token = "tok-that-must-not-be-logged"
    with caplog.at_level(logging.INFO):
        response = await browser.post(
            route[1], body_for(route[1], secret_token, invite_code=code), csrf=csrf
        )
    assert (response.status_code, reason(response)) == (400, "captcha_failed")
    assert log == ["verify"]  # not one database session of the route's own
    await nothing_happened(tenv, response)
    assert await tenv.scalar("SELECT count(*) FROM invites WHERE used_at IS NOT NULL") == 0
    assert f"captcha refused: action={action} why=invalid-input-response" in caplog.text
    assert secret_token not in caplog.text
    assert EMAIL not in caplog.text


@pytest.mark.parametrize(("route", "action"), ROUTES)
async def test_every_refusal_looks_the_same(tenv: Env, route: tuple[str, str], action: str) -> None:
    await user_with_password(tenv, "known@example.com")
    bodies: set[str] = set()
    for email in ("known@example.com", "unknown@example.com"):
        for token in (None, "", "x" * 2049, "tök", 42, ["a"], {"t": 1}, "fake-fail"):
            browser = Browser(tenv)
            payload = body_for(route[1], email=email)
            if token is not None:
                payload["captcha_token"] = token
            response = await browser.post(route[1], payload)
            assert response.status_code == 400, (token, response.text)
            bodies.add(response.text)
            assert not browser.signed_in
    assert bodies == {'{"detail":{"reason":"captcha_failed"}}'}
    assert await tenv.scalar("SELECT count(*) FROM auth_events") == 0
    await captcha_rows_only(tenv)


@pytest.mark.parametrize(("route", "action"), ROUTES)
async def test_a_request_without_a_token_fails_when_on(
    tenv: Env, route: tuple[str, str], action: str
) -> None:
    """The guard's behaviour half: each Turnstile route refuses a request with no token."""
    response = await Browser(tenv).post(route[1], body_for(route[1]))
    assert (response.status_code, reason(response)) == (400, "captcha_failed")


async def test_a_pass_signs_in_once_and_a_replayed_token_fails(tenv: Env) -> None:
    await user_with_password(tenv)
    first = Browser(tenv)
    ok = await first.post("/api/auth/login", body_for("/api/auth/login", "fake-pass-one"))
    assert ok.status_code == 200
    assert first.signed_in
    again = Browser(tenv)
    replay = await again.post("/api/auth/login", body_for("/api/auth/login", "fake-pass-one"))
    assert (replay.status_code, reason(replay)) == (400, "captcha_failed")
    assert not again.signed_in
    assert await tenv.scalar("SELECT count(*) FROM sessions") == 1


async def test_a_pass_still_runs_every_other_check(tenv: Env) -> None:
    await user_with_password(tenv)
    browser = Browser(tenv)
    wrong = body_for("/api/auth/login", "fake-pass-a", password="wrong password 12345")
    response = await browser.post("/api/auth/login", wrong)
    assert (response.status_code, reason(response)) == (401, "invalid_credentials")
    signup = await browser.post("/api/auth/signup", body_for("/api/auth/signup", "fake-pass-b"))
    assert (signup.status_code, reason(signup)) == (400, "invalid_invite")  # the invite gate stays


async def test_a_pass_mails_the_reset_and_a_replay_does_not(tenv: Env) -> None:
    await user_with_password(tenv)
    path = "/api/auth/password/reset/request"
    assert (await Browser(tenv).post(path, body_for(path, "fake-pass-r"))).status_code == 202
    assert len(tenv.app.state.mailer.sent) == 1
    replay = await Browser(tenv).post(path, body_for(path, "fake-pass-r"))
    assert (replay.status_code, reason(replay)) == (400, "captcha_failed")
    assert len(tenv.app.state.mailer.sent) == 1


async def test_a_pass_signs_up_with_an_invite(tenv: Env) -> None:
    async with tenv.db() as db:
        code = await auth.create_invite(db, timedelta(days=1))
    path = "/api/auth/signup"
    payload = body_for(path, "fake-pass-s", invite_code=code, email="new@example.com")
    assert (await Browser(tenv).post(path, payload)).status_code == 202
    assert await tenv.scalar("SELECT count(*) FROM pending_signups") == 1
    assert len(tenv.app.state.mailer.sent) == 1


@pytest.mark.parametrize(("route", "action"), ROUTES)
async def test_cloudflare_unavailable_is_503_with_retry_after(
    tenv: Env, route: tuple[str, str], action: str
) -> None:
    log: list[str] = []
    tenv.app.state.captcha = Recorder(log, CaptchaUnavailable("timeout"))
    browser = Browser(tenv)
    csrf = await browser.csrf()
    tenv.app.state.session_factory = SpyFactory(tenv.app.state.session_factory, log)
    response = await browser.post(route[1], body_for(route[1], "tok"), csrf=csrf)
    assert (response.status_code, reason(response)) == (503, "captcha_unavailable")
    assert response.headers["retry-after"] == "30"
    assert log == ["verify"]
    await nothing_happened(tenv, response)


async def test_the_fake_unavailable_token_and_misconfiguration_logs(
    tenv: Env, caplog: pytest.LogCaptureFixture
) -> None:
    path = "/api/auth/password/reset/request"
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.captcha"):
        response = await Browser(tenv).post(path, body_for(path, "fake-unavailable"))
    assert response.status_code == 503
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    caplog.clear()
    tenv.app.state.captcha = Recorder(
        [], CaptchaUnavailable("invalid-input-secret", misconfigured=True)
    )
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.captcha"):
        response = await Browser(tenv).post(path, body_for(path, "tok"))
    assert response.status_code == 503
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.ERROR, "captcha refused: action=reset why=invalid-input-secret")
    ]


async def test_on_but_not_wired_fails_closed(tenv: Env) -> None:
    tenv.app.state.captcha = None
    response = await Browser(tenv).post("/api/auth/login", body_for("/api/auth/login", "t"))
    assert (response.status_code, reason(response)) == (503, "captcha_unavailable")


async def test_the_captcha_bucket_caps_verifications_per_ip(tenv: Env) -> None:
    recorder = Recorder([], CaptchaFailed("invalid-input-response"))
    tenv.app.state.captcha = recorder
    browser = Browser(tenv)
    csrf = await browser.csrf()
    paths = [path for (_, path), _ in ROUTES]
    for i in range(60):  # every request counts, across the four routes
        path = paths[i % len(paths)]
        response = await browser.post(path, body_for(path, f"tok-{i}"), csrf=csrf)
        assert response.status_code == 400
    blocked = await browser.post("/api/auth/login", body_for("/api/auth/login", "t"), csrf=csrf)
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers
    assert len(recorder.calls) == 60  # the 61st made no call to Cloudflare
    assert CAPTCHA_IP.max_count == 60
    assert CAPTCHA_IP.window_seconds == 900


async def test_a_success_counts_too(tenv: Env) -> None:
    await user_with_password(tenv)
    browser = Browser(tenv)
    login = await browser.post("/api/auth/login", body_for("/api/auth/login", "fake-pass-1"))
    assert login.status_code == 200
    expected = tenv.app.state.limiter.bucket_hash(CAPTCHA_IP, bucket_ip(CLIENT_IP))
    count = await tenv.scalar("SELECT count FROM rate_limits WHERE bucket_hash = :h", h=expected)
    assert count == 1  # nothing is released


async def test_the_session_tells_the_page(tenv: Env, env_off: Env) -> None:
    on = (await Browser(tenv).get("/api/auth/session")).json()
    assert on["captcha"] == {"site_key": None, "fake": True}
    off = (await Browser(env_off).get("/api/auth/session")).json()
    assert off["captcha"] is None


@pytest.fixture
async def env_off(tenv: Env, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url) as env:
        yield env


async def test_off_nothing_is_asked_or_counted(env: Env) -> None:
    await user_with_password(env)
    browser = Browser(env)
    assert (await browser.sign_in(EMAIL)).status_code == 200  # no token at all
    captcha = env.app.state.limiter.bucket_hash(CAPTCHA_IP, bucket_ip(CLIENT_IP))
    assert (
        await env.scalar("SELECT count(*) FROM rate_limits WHERE bucket_hash = :h", h=captcha) == 0
    )


async def test_a_real_looking_site_key_reaches_the_session(
    clean_auth: None, app_database_url: str
) -> None:
    async with running_env(
        app_database_url,
        turnstile_enabled=True,
        turnstile_site_key="0x4AAAAAAAtestSiteKeyForTests",
        turnstile_secret_key="0x4AAAAAAAtestSecretForTestsOnly000000",
    ) as env:
        state = (await Browser(env).get("/api/auth/session")).json()
        assert state["captcha"] == {"site_key": "0x4AAAAAAAtestSiteKeyForTests", "fake": False}
        csp = (await env.client.get("/api/health")).headers["content-security-policy"]
        assert "script-src 'self' https://challenges.cloudflare.com;" in csp
        assert "0x4AAAAAAAtestSecretForTestsOnly000000" not in str(state)


async def test_the_fake_adds_no_cloudflare_source(tenv: Env) -> None:
    csp = (await tenv.client.get("/api/health")).headers["content-security-policy"]
    assert "challenges.cloudflare.com" not in csp
