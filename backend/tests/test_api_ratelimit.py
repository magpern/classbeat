"""The general limit on signed-in ``/api`` requests (#577, ADR 0003 section 9, amendment of
2026-11-05): a script with a stolen session is slowed down whatever route it calls.

Every signed-in ``/api`` request takes a slot, in one statement, in its buckets
(``ratelimit.api_buckets``): per user and minute and per user and hour, per user and minute for
writes or for the class and job reads, per client address and minute. Above any of them it is 429
``rate_limited`` with ``Retry-After``, answered by the middleware before CSRF and before the route.
Anonymous requests and the probes and sign-out are not counted. The tests use small limits from the
settings and pin the windows (#588), so a window boundary inside a test changes nothing.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from authtools import ORIGIN, Env, running_env, session_cookie
from classbeat.auth import ratelimit
from classbeat.auth.middleware import csrf_token_for_session
from classbeat.config import Settings

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #588

SESSION = "/api/auth/session"
WRITE = "/api/test/write"


@pytest.fixture
async def limited(clean_auth: None, app_database_url: str) -> AsyncIterator[Callable[..., Any]]:
    """An app with the general limit's counts overridden (the rest stays at the defaults)."""
    stack: list[Any] = []

    async def start(**limits: int) -> Env:
        context = running_env(app_database_url, **limits)
        env = await context.__aenter__()
        stack.append(context)
        calls: list[str] = []
        env.app.state.write_calls = calls

        @env.app.post(WRITE)
        async def write() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
            calls.append("write")
            return {"done": True}

        return env

    yield start
    for context in reversed(stack):
        await context.__aexit__(None, None, None)


class Signed:
    """A signed-in browser: the session cookie and its CSRF token on every request."""

    def __init__(self, env: Env, session_id: object, token: str) -> None:
        self.env = env
        self.headers = session_cookie(token)
        self.csrf = csrf_token_for_session(env.app.state.keys, session_id)

    async def get(self, path: str) -> httpx.Response:
        return await self.env.client.get(path, headers=self.headers)

    async def post(self, path: str, csrf: str | None = None) -> httpx.Response:
        headers = {**self.headers, "Origin": ORIGIN, "X-CSRF-Token": csrf or self.csrf}
        return await self.env.client.post(path, headers=headers)


async def signed(env: Env, email: str = "user@example.com") -> Signed:
    issued = await env.login(await env.user(email))
    return Signed(env, issued.id, issued.token)


def assert_limited(response: httpx.Response, window: int = 60) -> None:
    assert response.status_code == 429, response.text
    assert response.json() == {"detail": {"reason": "rate_limited"}}
    assert 0 < int(response.headers["Retry-After"]) <= window


def test_the_defaults_are_generous_and_writes_and_reads_are_tighter() -> None:
    fields = Settings.model_fields
    defaults = {
        name: fields[name].default
        for name in (
            "api_requests_per_minute",
            "api_requests_per_hour",
            "api_writes_per_minute",
            "api_reads_per_minute",
            "api_ip_requests_per_minute",
        )
    }
    assert defaults == {
        "api_requests_per_minute": 300,
        "api_requests_per_hour": 3000,
        "api_writes_per_minute": 60,
        "api_reads_per_minute": 120,
        "api_ip_requests_per_minute": 1200,
    }


def _shape(buckets: list[ratelimit.Bucket]) -> list[tuple[str, str, int, int]]:
    return [
        (b.limit.action, b.limit.scope, b.limit.max_count, b.limit.window_seconds) for b in buckets
    ]


@pytest.mark.parametrize(
    ("method", "path", "extra"),
    [
        ("GET", "/api/auth/session", None),
        ("GET", "/api/usage", None),
        ("POST", "/api/jobs", ("api-write", 60)),
        ("DELETE", "/api/classes/x", ("api-write", 60)),
        ("PATCH", "/api/account", ("api-write", 60)),
        ("GET", "/api/classes", ("api-read", 120)),
        ("GET", "/api/classes/x/revisions", ("api-read", 120)),
        ("HEAD", "/api/jobs/x", ("api-read", 120)),
        ("GET", "/api/jobs/x/events", ("api-read", 120)),
        ("GET", "/api/classesx", None),
    ],
)
def test_the_buckets_of_a_request(method: str, path: str, extra: tuple[str, int] | None) -> None:
    settings = Settings.model_construct(**{k: f.default for k, f in Settings.model_fields.items()})
    buckets = ratelimit.api_buckets(settings, "u1", "203.0.113.9", method, path)
    expected = [("api", "account", 300, 60), ("api", "account", 3000, 3600)]
    if extra is not None:
        expected.append((extra[0], "account", extra[1], 60))
    expected.append(("api", "ip", 1200, 60))
    assert _shape(buckets) == expected
    assert [b.value for b in buckets[:-1]] == ["u1"] * (len(buckets) - 1)
    assert buckets[-1].value == "203.0.113.9"  # an IPv4 address is its own bucket


@pytest.mark.parametrize(
    ("path", "limited"),
    [
        ("/api/auth/session", True),
        ("/api", True),
        ("/api/admin/users", True),
        ("/api/health", False),
        ("/api/ready", False),
        ("/api/auth/logout", False),
        ("/api/auth/logout-all", False),
        ("/oauth/google/callback", False),
        ("/classes", False),
        ("/apix", False),
    ],
)
def test_which_paths_are_limited(path: str, limited: bool) -> None:
    assert ratelimit.api_limited(path) is limited


async def test_a_user_over_the_minute_limit_gets_429_and_another_user_does_not(
    limited: Callable[..., Any],
) -> None:
    env: Env = await limited(api_requests_per_minute=5)
    mine, theirs = await signed(env), await signed(env, "other@example.com")
    for _ in range(5):
        assert (await mine.get(SESSION)).status_code == 200
    assert_limited(await mine.get(SESSION))
    assert_limited(await mine.get("/api/usage"))  # every route, not only the one it hammered
    assert (await theirs.get(SESSION)).status_code == 200


async def test_the_hour_limit_counts_every_request(limited: Callable[..., Any]) -> None:
    env: Env = await limited(api_requests_per_hour=3)
    browser = await signed(env)
    for _ in range(3):
        assert (await browser.get(SESSION)).status_code == 200
    assert_limited(await browser.get(SESSION), window=3600)


async def test_writes_have_a_tighter_limit_and_the_route_never_runs_above_it(
    limited: Callable[..., Any],
) -> None:
    env: Env = await limited(api_writes_per_minute=2)
    browser = await signed(env)
    for _ in range(2):
        assert (await browser.post(WRITE)).status_code == 200
    assert_limited(await browser.post(WRITE))
    assert env.app.state.write_calls == ["write", "write"]
    assert (await browser.get(SESSION)).status_code == 200  # reads are not writes


async def test_a_limited_write_is_429_before_the_csrf_check(limited: Callable[..., Any]) -> None:
    env: Env = await limited(api_writes_per_minute=1)
    browser = await signed(env)
    assert (await browser.post(WRITE, csrf="wrong")).status_code == 403  # counted all the same
    assert_limited(await browser.post(WRITE))
    assert env.app.state.write_calls == []


async def test_the_class_and_job_reads_have_a_tighter_limit(limited: Callable[..., Any]) -> None:
    env: Env = await limited(api_reads_per_minute=2)
    browser = await signed(env)
    for _ in range(2):
        assert (await browser.get("/api/classes")).status_code == 200
    assert_limited(await browser.get("/api/classes"))
    assert (await browser.get(SESSION)).status_code == 200  # other reads go on


async def test_the_client_address_limit_counts_every_signed_in_user(
    limited: Callable[..., Any],
) -> None:
    env: Env = await limited(api_ip_requests_per_minute=3)
    mine, theirs = await signed(env), await signed(env, "other@example.com")
    assert (await mine.get(SESSION)).status_code == 200
    assert (await mine.get(SESSION)).status_code == 200
    assert (await theirs.get(SESSION)).status_code == 200
    assert_limited(await theirs.get(SESSION))  # the same address (the test client's)


async def test_anonymous_requests_are_not_counted(limited: Callable[..., Any]) -> None:
    env: Env = await limited(api_requests_per_minute=1, api_ip_requests_per_minute=1)
    for _ in range(3):
        assert (await env.client.get(SESSION)).status_code == 200
        env.client.cookies.clear()
    browser = await signed(env)
    assert (await browser.get(SESSION)).status_code == 200  # the address's slot was still free
    assert_limited(await browser.get(SESSION))


async def test_an_unknown_session_token_is_anonymous_and_not_counted(
    limited: Callable[..., Any],
) -> None:
    env: Env = await limited(api_requests_per_minute=1)
    for _ in range(3):
        answer = await env.client.get(SESSION, headers=session_cookie("not-a-session"))
        assert answer.status_code == 200
        assert answer.json()["user"] is None


async def test_the_probes_and_sign_out_are_never_limited(limited: Callable[..., Any]) -> None:
    env: Env = await limited(api_requests_per_minute=1, api_writes_per_minute=1)
    browser = await signed(env)
    other = await signed(env, "other@example.com")
    assert (await browser.get(SESSION)).status_code == 200
    assert_limited(await browser.get(SESSION))
    for path in ("/api/health", "/api/ready"):
        assert (await env.client.get(path, headers=browser.headers)).status_code == 200
    # Signing out every session (the script's included) works with the buckets used up.
    assert (await other.post("/api/auth/logout")).status_code == 204
    assert (await browser.post("/api/auth/logout-all")).status_code == 204
    assert (await browser.get(SESSION)).json()["user"] is None


async def test_parallel_requests_cannot_overshoot(limited: Callable[..., Any]) -> None:
    env: Env = await limited(api_requests_per_minute=5)
    browser = await signed(env)
    answers = await asyncio.gather(*(browser.get(SESSION) for _ in range(15)))
    assert sorted(a.status_code for a in answers) == [200] * 5 + [429] * 10


async def test_two_users_on_one_address_in_parallel_do_not_deadlock(
    limited: Callable[..., Any],
) -> None:
    """Both share the address bucket; the rows are locked in hash order, so no request waits on
    another in a cycle (a deadlock would be a 500)."""
    env: Env = await limited()
    one, two = await signed(env), await signed(env, "other@example.com")
    answers = await asyncio.gather(
        *(b.get(path) for _ in range(10) for b in (one, two) for path in (SESSION, "/api/classes"))
    )
    assert {a.status_code for a in answers} == {200}


@pytest.mark.parametrize("windows_back", [0, 1, 2])
async def test_the_refusal_does_not_depend_on_the_clock_window(
    limited: Callable[..., Any],
    freeze_rate_windows: Callable[[float | None], float],
    windows_back: int,
) -> None:
    """#588: the windows are pinned, so a database clock in a later window changes nothing."""
    freeze_rate_windows(time.time() - windows_back * 60)
    env: Env = await limited(api_requests_per_minute=3)
    browser = await signed(env)
    for _ in range(3):
        assert (await browser.get(SESSION)).status_code == 200
    assert_limited(await browser.get(SESSION))


async def test_the_table_holds_no_user_id_or_address_in_the_clear(
    limited: Callable[..., Any],
) -> None:
    env: Env = await limited()
    browser = await signed(env)
    assert (await browser.get(SESSION)).status_code == 200
    user_id = await env.scalar("SELECT id FROM users WHERE email = 'user@example.com'")
    rows = await env.scalar(
        "SELECT count(*) FROM rate_limits WHERE position(CAST(:u AS bytea) IN bucket_hash) > 0 "
        "OR position(CAST('127.0.0.1' AS bytea) IN bucket_hash) > 0",
        u=str(user_id),
    )
    assert rows == 0
    assert await env.scalar("SELECT count(*) FROM rate_limits") == 3  # minute, hour, address
