"""SR-05 (#354): ``GET /api/music/mowl-playlists`` is rate limited like the other provider-calling
routes (``auth.mowl_playlists_buckets``, the fixed-window limiter in Postgres): 30 per user and 60
per IP per 10 minutes, every request counts, and a refusal comes before the connection is opened
and before any MOWL call. Against the fake MOWL; never the network."""

import asyncio
from typing import Any

import pytest

from classbeat.auth import ratelimit
from designtools import DesignEnv, FakeMowlMusic
from jobapi import Browser
from optools import TOKEN_A, TOKEN_B

pytestmark = pytest.mark.usefixtures("jenv")

URL = "/api/music/mowl-playlists"
USER_LIMIT = ratelimit.MOWL_PLAYLISTS_USER.max_count
IP_LIMIT = ratelimit.MOWL_PLAYLISTS_IP.max_count


def test_the_limits_are_per_user_and_per_ip_and_the_ip_is_wider() -> None:
    assert (USER_LIMIT, ratelimit.MOWL_PLAYLISTS_USER.scope) == (30, "account")
    assert (IP_LIMIT, ratelimit.MOWL_PLAYLISTS_IP.scope) == (60, "ip")
    assert ratelimit.MOWL_PLAYLISTS_USER.window_seconds == 600


async def test_a_request_over_the_user_limit_answers_429_before_any_mowl_call(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    browser: Browser = await browsers(mowl=False)
    await denv.ops.connect(browser.user_id, TOKEN_A)
    for _ in range(USER_LIMIT):
        assert (await browser.get(URL)).status_code == 200
    refused = await browser.get(URL)
    assert refused.status_code == 429
    assert refused.json() == {"detail": {"reason": "rate_limited"}}
    assert 0 < int(refused.headers["Retry-After"]) <= 600
    assert len(fake_mowl.reads) == USER_LIMIT  # the refused one never reached MOWL


async def test_parallel_requests_cannot_overshoot_the_limit(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    browser: Browser = await browsers(mowl=False)
    await denv.ops.connect(browser.user_id, TOKEN_A)
    answers = await asyncio.gather(*(browser.get(URL) for _ in range(USER_LIMIT + 15)))
    codes = sorted(a.status_code for a in answers)
    assert codes == [200] * USER_LIMIT + [429] * 15
    assert len(fake_mowl.reads) == USER_LIMIT  # one MOWL call per admitted request, no more
    assert all(
        a.json() == {"detail": {"reason": "rate_limited"}} for a in answers if a.status_code == 429
    )


async def test_one_user_over_the_limit_does_not_limit_another_user(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    mine: Browser = await browsers(mowl=False)
    other: Browser = await browsers(mowl=False)
    await denv.ops.connect(mine.user_id, TOKEN_A)
    await denv.ops.connect(other.user_id, TOKEN_B)
    for _ in range(USER_LIMIT):
        await mine.get(URL)
    assert (await mine.get(URL)).status_code == 429
    assert (await other.get(URL)).status_code == 200
    assert fake_mowl.tokens_read.count(TOKEN_B) == 1


async def test_the_ip_limit_holds_across_users(
    browsers: Any, denv: DesignEnv, fake_mowl: FakeMowlMusic
) -> None:
    riders: list[Browser] = [await browsers(mowl=False) for _ in range(3)]
    for rider, token in zip(riders, (TOKEN_A, TOKEN_B, TOKEN_A), strict=True):
        await denv.ops.connect(rider.user_id, token)
    per_rider = IP_LIMIT // 3
    assert per_rider < USER_LIMIT  # no user limit is reached below
    for rider in riders:
        for _ in range(per_rider):
            assert (await rider.get(URL)).status_code == 200
    refused = await riders[0].get(URL)  # this user is far below its own limit
    assert refused.status_code == 429
    assert len(fake_mowl.reads) == per_rider * 3


async def test_a_refusal_comes_before_the_connection_is_opened(
    browsers: Any, fake_mowl: FakeMowlMusic
) -> None:
    """Without a MOWL connection the answer is 409 until the limit; then 429, whatever the state."""
    browser: Browser = await browsers(mowl=False)
    for _ in range(USER_LIMIT):
        assert (await browser.get(URL)).status_code == 409
    assert (await browser.get(URL)).status_code == 429
    assert fake_mowl.requests == []
