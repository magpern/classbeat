"""The provider's rate limits (ADR 0034 section 1, ADR 0003 section 9), windows pinned (#588)."""

import logging
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from authtools import Env
from classbeat.auth.oidc import limits, token_routes
from classbeat.auth.ratelimit import Bucket, Limit
from oidctools import FakePlayer, basic, flow_cookie_of, oidc_env, query_of

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")


@pytest.fixture
async def oenv(clean_auth: None, app_database_url: str, kek_dir: Path) -> AsyncIterator[Env]:
    async with oidc_env(app_database_url, kek_dir) as running:
        yield running


def test_the_limits_are_the_pinned_ones() -> None:
    pinned = {
        limits.AUTHORIZE_IP: ("ip", 120, 3600),
        limits.AUTHORIZE_USER: ("account", 30, 3600),
        limits.CONSENT_USER: ("account", 20, 3600),
        limits.TOKEN_ALL: ("global", 600, 60),
        limits.TOKEN_FAIL_IP: ("ip", 20, 600),
        limits.CLIENT_FAIL_WARN: ("client", 100, 3600),
    }
    for limit, (scope, most, window) in pinned.items():
        assert (limit.scope, limit.max_count, limit.window_seconds) == (scope, most, window)


async def test_authorize_answers_429_above_120_per_address(oenv: Env) -> None:
    player = FakePlayer(oenv)
    for _ in range(120):
        assert (await player.authorize()).status_code == 302
    refused = await player.authorize()
    assert refused.status_code == 429
    assert "retry-after" in refused.headers


async def test_continue_answers_429_above_30_per_user(oenv: Env) -> None:
    user_id = await oenv.user()
    session = (await oenv.login(user_id)).token
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    for _ in range(30):
        shown = await player.follow(started.headers["location"], flow, session)
        assert shown.status_code == 302
    assert (await player.follow(started.headers["location"], flow, session)).status_code == 429


async def test_consent_answers_429_above_20_per_user(oenv: Env) -> None:
    user_id = await oenv.user()
    session = (await oenv.login(user_id)).token
    player = FakePlayer(oenv)
    started = await player.authorize()
    flow = flow_cookie_of(started)
    shown = await player.follow(started.headers["location"], flow, session)
    request_id = query_of(shown.headers["location"])["r"]
    other = flow_cookie_of(await FakePlayer(oenv).authorize())
    for _ in range(20):  # refused by the binding, still counted
        assert (await player.consent(request_id, other, session)).status_code == 400
    assert (await player.consent(request_id, flow, session)).status_code == 429


async def test_token_failures_answer_429_above_20_per_address(oenv: Env) -> None:
    user_id = await oenv.user()
    session = (await oenv.login(user_id)).token
    player = FakePlayer(oenv)
    code = await player.code_for(session)
    for _ in range(20):
        failed = await player.token("x", headers=basic(secret="z" * 43))
        assert failed.status_code == 401
    blocked = await player.token(code)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.headers["cache-control"] == "no-store"


async def test_token_successes_do_not_count_per_address(oenv: Env) -> None:
    user_id = await oenv.user()
    session = (await oenv.login(user_id)).token
    for _ in range(22):
        player = FakePlayer(oenv)
        assert (await player.token(await player.code_for(session))).status_code == 200


async def test_client_failures_only_warn(
    oenv: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    low = Limit("oidc-client-fail-test", "client", 1, 3600)
    monkeypatch.setattr(token_routes, "client_warning_buckets", lambda c: [Bucket(low, c)])
    player = FakePlayer(oenv)
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.oidc.token_routes"):
        for _ in range(3):
            assert (await player.token("x", headers=basic(secret="z" * 43))).status_code == 401
    warnings = [r for r in caplog.records if "authentication failures" in r.getMessage()]
    assert len(warnings) == 2
