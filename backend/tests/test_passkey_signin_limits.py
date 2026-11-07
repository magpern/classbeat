"""The passkey sign-in limits of ADR 0019 section 10 (#773).

Start: per address (60 / 5 min, IPv6 per /64), per IPv6 /48 (300) and for everyone (20 000),
reserved in that order and stopping at the first refusal, so a refused start takes no slot in a
wider bucket. The address and /48 answer 429, the global bucket 503 ``passkey_busy``; password
sign-in uses other buckets. Failures: per address and per account (unless a known device),
released on success.
"""

import asyncio
import time

import pytest

from authtools import Env
from classbeat.auth.clientip import bucket_ip, bucket_ip48
from classbeat.auth.passkey_limits import (
    GLOBAL_VALUE,
    PASSKEY_SIGNIN_FAIL_ACCOUNT,
    PASSKEY_SIGNIN_FAIL_IP,
    PASSKEY_SIGNIN_START_ALL,
    PASSKEY_SIGNIN_START_IP,
    PASSKEY_SIGNIN_START_IP48,
    start_buckets,
)
from classbeat.auth.passkey_signin_routes import reserve_in_order
from classbeat.auth.ratelimit import Limit
from flows import PASSWORD
from passkeysignin import anonymous, sign_in_with_passkey, start_signin, with_passkey
from passkeytools import HOME, reason
from passkeytools import penv as penv

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

V6 = "2001:db8:aa:{}::7"  # /64 number {} of the /48 2001:db8:aa::/48


async def hits(env: Env, limit: Limit, value: str) -> int:
    digest = env.app.state.limiter.bucket_hash(limit, value)
    found = await env.scalar(
        "SELECT coalesce(sum(count), 0) FROM rate_limits WHERE bucket_hash = :h", h=digest
    )
    return int(found)  # type: ignore[arg-type]


async def fill(env: Env, ip: str, n: int) -> None:
    """``n`` starts from ``ip`` through the same ordered reservation the route uses."""
    for _ in range(n):
        await reserve_in_order(env.app.state.limiter, start_buckets(ip))


def test_the_start_buckets_are_narrowest_first() -> None:
    v4 = start_buckets("198.51.100.7")
    assert [b.bucket.limit for b in v4] == [PASSKEY_SIGNIN_START_IP, PASSKEY_SIGNIN_START_ALL]
    assert [b.busy for b in v4] == [False, True]
    v6 = start_buckets(V6.format(1))
    assert [b.bucket.limit for b in v6] == [
        PASSKEY_SIGNIN_START_IP,
        PASSKEY_SIGNIN_START_IP48,
        PASSKEY_SIGNIN_START_ALL,
    ]
    assert v6[0].bucket.value == "2001:db8:aa:1::/64"
    assert v6[1].bucket.value == "2001:db8:aa::/48"
    assert (PASSKEY_SIGNIN_START_IP.max_count, PASSKEY_SIGNIN_START_IP48.max_count) == (60, 300)
    assert PASSKEY_SIGNIN_START_ALL.max_count == 20_000
    windows = {PASSKEY_SIGNIN_START_IP.window_seconds, PASSKEY_SIGNIN_START_ALL.window_seconds}
    assert windows == {300}


async def test_the_61st_start_from_one_address_is_429(penv: Env) -> None:
    client = anonymous(penv)
    for _ in range(60):
        assert (await start_signin(client)).status_code == 200
    refused = await start_signin(client)
    assert (refused.status_code, reason(refused)) == (429, "rate_limited")
    assert int(refused.headers["Retry-After"]) >= 1
    assert await hits(penv, PASSKEY_SIGNIN_START_ALL, GLOBAL_VALUE) == 60


async def test_a_thousand_starts_from_one_address_leave_the_global_count_at_60(penv: Env) -> None:
    await fill(penv, HOME, 1000)
    assert await hits(penv, PASSKEY_SIGNIN_START_IP, bucket_ip(HOME)) == 1000  # refused count too
    assert await hits(penv, PASSKEY_SIGNIN_START_ALL, GLOBAL_VALUE) == 60


async def test_parallel_starts_count_exactly_in_the_one_global_row(penv: Env) -> None:
    """Section 10: the global bucket is one hot row; parallel upserts on it neither lose nor
    double a count. 200 starts from 200 addresses, 20 at a time."""
    limiter = penv.app.state.limiter
    began = time.monotonic()
    for batch in range(10):
        addresses = [f"198.51.{batch}.{n}" for n in range(1, 21)]
        refused = await asyncio.gather(
            *(reserve_in_order(limiter, start_buckets(ip)) for ip in addresses)
        )
        assert refused == [None] * 20
    elapsed = time.monotonic() - began
    assert await hits(penv, PASSKEY_SIGNIN_START_ALL, GLOBAL_VALUE) == 200
    assert elapsed < 60  # a generous cap; locally about a second (the number goes in the PR)


async def test_the_48_counts_only_starts_its_64_allowed_and_refuses_the_301st(
    penv: Env,
) -> None:
    await fill(penv, V6.format(1), 100)  # 60 allowed, 40 refused by the /64
    wide = bucket_ip48(V6.format(1))
    assert wide is not None
    assert await hits(penv, PASSKEY_SIGNIN_START_IP48, wide) == 60
    for n in range(2, 6):
        await fill(penv, V6.format(n), 60)
    assert await hits(penv, PASSKEY_SIGNIN_START_IP48, wide) == 300
    assert await hits(penv, PASSKEY_SIGNIN_START_ALL, GLOBAL_VALUE) == 300
    refused = await start_signin(anonymous(penv, V6.format(6)))  # a fresh /64 of the same /48
    assert (refused.status_code, reason(refused)) == (429, "rate_limited")
    assert await hits(penv, PASSKEY_SIGNIN_START_IP, bucket_ip(V6.format(6))) == 1
    assert await hits(penv, PASSKEY_SIGNIN_START_IP48, wide) == 301
    assert await hits(penv, PASSKEY_SIGNIN_START_ALL, GLOBAL_VALUE) == 300  # never reached
    other = await start_signin(anonymous(penv, "2001:db8:bb:1::7"))  # another /48
    assert other.status_code == 200


async def test_a_full_global_bucket_is_503_passkey_busy_and_passwords_still_work(
    penv: Env,
) -> None:
    holder = await with_passkey(penv)
    await fill(penv, "192.0.2.1", 1)
    digest = penv.app.state.limiter.bucket_hash(PASSKEY_SIGNIN_START_ALL, GLOBAL_VALUE)
    await penv.run("UPDATE rate_limits SET count = 20000 WHERE bucket_hash = :h", h=digest)
    client = anonymous(penv)
    busy = await start_signin(client)
    assert (busy.status_code, reason(busy)) == (503, "passkey_busy")
    assert int(busy.headers["Retry-After"]) >= 1
    password = await client.post("/api/auth/login", {"email": holder.email, "password": PASSWORD})
    assert password.status_code == 200, password.text


async def test_failures_from_one_address_are_limited_and_a_success_releases(penv: Env) -> None:
    holder = await with_passkey(penv)
    client = anonymous(penv)
    assert (await sign_in_with_passkey(client, holder, uv=False)).status_code == 400
    assert await hits(penv, PASSKEY_SIGNIN_FAIL_IP, bucket_ip(HOME)) == 1
    assert (await sign_in_with_passkey(client, holder)).status_code == 200
    assert await hits(penv, PASSKEY_SIGNIN_FAIL_IP, bucket_ip(HOME)) == 1  # its own released
    await penv.run(
        "UPDATE rate_limits SET count = 30 WHERE bucket_hash = :h",
        h=penv.app.state.limiter.bucket_hash(PASSKEY_SIGNIN_FAIL_IP, bucket_ip(HOME)),
    )
    refused = await sign_in_with_passkey(anonymous(penv), holder)
    assert (refused.status_code, reason(refused)) == (429, "rate_limited")


async def test_failures_count_per_account_except_from_a_known_device(penv: Env) -> None:
    holder = await with_passkey(penv)
    account = str(holder.user_id)
    known = await sign_in_with_passkey(holder.browser, holder, uv=False)
    assert known.status_code == 400
    assert await hits(penv, PASSKEY_SIGNIN_FAIL_ACCOUNT, account) == 0  # a known device
    stranger = anonymous(penv, "203.0.113.99")
    assert (await sign_in_with_passkey(stranger, holder, uv=False)).status_code == 400
    assert await hits(penv, PASSKEY_SIGNIN_FAIL_ACCOUNT, account) == 1
    await penv.run(
        "UPDATE rate_limits SET count = 20 WHERE bucket_hash = :h",
        h=penv.app.state.limiter.bucket_hash(PASSKEY_SIGNIN_FAIL_ACCOUNT, account),
    )
    refused = await sign_in_with_passkey(anonymous(penv, "203.0.113.98"), holder)
    assert (refused.status_code, reason(refused)) == (429, "rate_limited")
    assert (await sign_in_with_passkey(holder.browser, holder)).status_code == 200
