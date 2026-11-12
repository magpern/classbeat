"""#998 (from #995): a ``rate_limits`` row lock never outlives the limiter's own statement.

The general limit runs on every signed-in ``/api`` request, so every request of a user (and every
signed-in request from one address) upserts the same rows. Postgres keeps a row lock until the
transaction ends; if the limiter's transaction stayed open after its upsert, a stalled or cancelled
coroutine (an await, a busy event loop, a request torn down before its ``COMMIT``) would keep the
lock, and every other request on those rows would wait the role's 5 s ``lock_timeout`` and fail.
The upsert therefore runs as its own implicit transaction: Postgres commits it before it answers.

The tests suspend (or cancel) the limiter right after its upsert has returned and probe the rows
from a second connection with ``FOR UPDATE NOWAIT``, which fails at once on a held lock. A lock
timeout that still happens (another holder, a slow disk) is a 503 with a short ``Retry-After``, and
the request is never served: the limit is never skipped.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.sql.dml import Delete, Insert, Update

from authtools import ORIGIN, Env, running_env, session_cookie
from classbeat.auth import limit_writes, ratelimit
from classbeat.auth.limit_writes import LimitBusy
from classbeat.auth.middleware import LIMIT_BUSY_RETRY_AFTER, csrf_token_for_session
from classbeat.db import user_session
from ctx import make_context

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #588

SESSION = "/api/auth/session"
WRITE = "/api/test/write"
SUCCEED = "/api/test/succeed"
PROBE = "SELECT count(*) FROM (SELECT 1 FROM rate_limits FOR UPDATE NOWAIT) AS locked"


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url) as env:
        calls: list[str] = []
        env.app.state.write_calls = calls

        @env.app.post(WRITE)
        async def write() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
            calls.append("write")
            return {"done": True}

        yield env


@asynccontextmanager
async def raw(app_database_url: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """A second connection as the app role, outside the app's pool."""
    url = app_database_url.replace("postgresql+psycopg://", "postgresql://")
    connection = await psycopg.AsyncConnection.connect(url, autocommit=True)
    try:
        yield connection
    finally:
        await connection.close()


async def probe(app_database_url: str) -> int:
    """The number of ``rate_limits`` rows; raises ``LockNotAvailable`` if any is locked."""
    async with raw(app_database_url) as connection:
        row = await (await connection.execute(PROBE)).fetchone()
        assert row is not None
        return int(row[0])


def _is_limiter_write(statement: object) -> bool:
    """The limiter's upsert, or a release's update or delete (#1006)."""
    return isinstance(statement, Insert | Update | Delete) and statement.table.name == "rate_limits"  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


def gate_after_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[asyncio.Event, asyncio.Event]:
    """Suspend the limiter right after its upsert (or a release's write) has returned, whatever way
    it executes it (a session or a connection): the moment a slow or cancelled request would still
    hold it."""
    reached, release = asyncio.Event(), asyncio.Event()

    def wrap(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def execute(self: object, statement: object, *args: object, **kwargs: object) -> Any:
            result = await original(self, statement, *args, **kwargs)
            if _is_limiter_write(statement):
                reached.set()
                await release.wait()
            return result

        return execute

    monkeypatch.setattr(AsyncSession, "execute", wrap(AsyncSession.execute))
    monkeypatch.setattr(AsyncConnection, "execute", wrap(AsyncConnection.execute))
    return reached, release


def buckets(env: Env) -> list[ratelimit.Bucket]:
    return ratelimit.api_buckets(env.settings, "u1", "203.0.113.9", "GET", "/api/classes")


async def test_a_suspended_limiter_holds_no_row_lock(
    env: Env, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rows exist (an earlier request), so the upsert updates them: the e2e failure of #995,
    where a second request waited on the first one's update past the lock timeout."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    assert (await limiter.begin_together(buckets(env))).allowed
    reached, release = gate_after_upsert(monkeypatch)
    task = asyncio.create_task(limiter.begin_together(buckets(env)))
    await asyncio.wait_for(reached.wait(), 10)
    try:
        assert await probe(app_database_url) == 4  # minute, hour, read, address; none locked
    finally:
        release.set()
    attempt = await task
    assert attempt.allowed
    assert {slot.window_start for slot in attempt.slots}
    assert await env.scalar("SELECT sum(count) FROM rate_limits") == 8  # counted, both times


async def test_a_suspended_first_upsert_holds_no_lock_either(
    env: Env, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new window inserts its rows; they are committed (visible) as soon as the upsert returns."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    reached, release = gate_after_upsert(monkeypatch)
    task = asyncio.create_task(limiter.begin_together(buckets(env)))
    await asyncio.wait_for(reached.wait(), 10)
    try:
        assert await probe(app_database_url) == 4
    finally:
        release.set()
    assert (await task).allowed


async def test_a_cancelled_limiter_leaves_no_lock_and_the_slot_counted(
    env: Env, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request torn down after its upsert: the slot stays taken (never fail open) and no lock or
    open transaction is left behind on a pooled connection."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    assert (await limiter.begin_together(buckets(env))).allowed
    reached, _release = gate_after_upsert(monkeypatch)
    task = asyncio.create_task(limiter.begin_together(buckets(env)))
    await asyncio.wait_for(reached.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await probe(app_database_url) == 4
    assert await env.scalar("SELECT sum(count) FROM rate_limits") == 8


async def test_the_reserve_of_the_other_limits_holds_no_lock(
    env: Env, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``begin`` (sign-in, connect, export ...) reserves bucket by bucket the same way."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    one = [ratelimit.Bucket(ratelimit.EXPORT_USER, "u1")]
    assert (await limiter.begin(one)).allowed
    reached, release = gate_after_upsert(monkeypatch)
    task = asyncio.create_task(limiter.begin(one))
    await asyncio.wait_for(reached.wait(), 10)
    try:
        assert await probe(app_database_url) == 1
    finally:
        release.set()
    assert (await task).allowed


def sign_in() -> list[ratelimit.Bucket]:
    """The pair (deleted on success), the account and the address: in that order."""
    return ratelimit.password_buckets("user@example.com", "203.0.113.9", known_device=False)


async def counts(env: Env, wanted: list[ratelimit.Bucket]) -> list[object]:
    """Each bucket's count summed over its windows; ``None`` once its rows are gone."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    sql = "SELECT sum(count) FROM rate_limits WHERE bucket_hash = :h"
    return [await env.scalar(sql, h=limiter.bucket_hash(b.limit, b.value)) for b in wanted]


async def test_a_suspended_succeed_holds_no_row_lock(
    env: Env, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1006: a success gives its slots back one statement at a time, each committed at once."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    attempt = await limiter.begin(sign_in())
    reached, release = gate_after_upsert(monkeypatch)
    task = asyncio.create_task(attempt.succeed())
    await asyncio.wait_for(reached.wait(), 10)
    try:
        assert await probe(app_database_url) == 2  # the pair is deleted; none of the rest locked
    finally:
        release.set()
    await task
    assert await counts(env, sign_in()) == [None, 0, 0]
    assert attempt.slots == []


async def test_a_cancelled_succeed_gives_each_slot_back_at_most_once(
    env: Env, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Torn down after its first write: no lock is left, the slots it did not reach stay counted
    (never fail open), and a later call gives back only those, once. Buckets that all decrement,
    so a slot given back twice would show."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    connect = ratelimit.connect_buckets("mowl", "u1", "203.0.113.9", "user@example.com")
    assert (await limiter.begin(connect)).allowed  # an earlier failure: must stay counted
    attempt = await limiter.begin(connect)
    reached, release = gate_after_upsert(monkeypatch)
    task = asyncio.create_task(attempt.succeed())
    await asyncio.wait_for(reached.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await probe(app_database_url) == 3
    assert await counts(env, connect) == [1, 2, 2]
    assert len(attempt.slots) == 2
    release.set()
    await attempt.succeed()
    await attempt.succeed()  # nothing left to give back
    assert await counts(env, connect) == [1, 1, 1]


async def test_a_suspended_release_holds_no_row_lock(
    env: Env, app_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``RateLimiter.release`` (the Spotify callback) gives back bucket by bucket the same way."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    start = ratelimit.spotify_start_buckets("u1", "203.0.113.9")
    assert (await limiter.begin(start)).allowed
    reached, release = gate_after_upsert(monkeypatch)
    task = asyncio.create_task(limiter.release(start))
    await asyncio.wait_for(reached.wait(), 10)
    try:
        assert await probe(app_database_url) == 2
    finally:
        release.set()
    await task
    assert await counts(env, start) == [0, 0]


@pytest.mark.parametrize("call", ["begin", "succeed", "release"])
async def test_a_lock_timeout_in_a_limiter_write_is_busy(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, call: str
) -> None:
    """55P03 in any reserve or release is ``LimitBusy`` (the 503), logged without values; every
    other database error stays a ``DBAPIError`` (the redacted 500)."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    attempt = await limiter.begin(sign_in())
    run = {
        "begin": lambda: limiter.begin(sign_in()),
        "succeed": attempt.succeed,
        "release": lambda: limiter.release(sign_in()),
    }[call]
    for error, expected in ((_LockTimeout, LimitBusy), (_OtherError, OperationalError)):

        async def failing(*_: object, error: type[Exception] = error) -> Any:
            raise OperationalError("UPDATE planted@example.com", {}, error("planted"))

        monkeypatch.setattr(limit_writes, "system_statement", failing)
        with pytest.raises(expected):
            await run()
    assert "planted" not in caplog.text
    assert await counts(env, sign_in()) == [1, 1, 1]  # nothing given back, nothing counted twice


async def test_a_real_lock_timeout_in_succeed_is_503_and_the_slots_stay_counted(
    env: Env, app_database_url: str
) -> None:
    """End to end: another transaction holds the account bucket's row past the role's 5 s lock
    timeout while a route gives its slots back. The answer is the general limit's 503 ``busy``,
    and the slots not given back stay taken."""
    limiter: ratelimit.RateLimiter = env.app.state.limiter
    reserved, go = asyncio.Event(), asyncio.Event()
    done: list[str] = []

    @env.app.get(SUCCEED)
    async def succeed() -> dict[str, bool]:  # pyright: ignore[reportUnusedFunction]
        attempt = await limiter.begin(sign_in())
        reserved.set()
        await go.wait()
        await attempt.succeed()
        done.append("succeed")
        return {"done": True}

    account = sign_in()[1]
    async with raw(app_database_url) as holder, holder.transaction():
        request = asyncio.create_task(env.client.get(SUCCEED))  # anonymous: no general limit
        await asyncio.wait_for(reserved.wait(), 10)
        await holder.execute(
            "SELECT 1 FROM rate_limits WHERE bucket_hash = %s FOR UPDATE",
            (limiter.bucket_hash(account.limit, account.value),),
        )
        go.set()
        assert_busy(await asyncio.wait_for(request, 30))
    assert done == []
    assert await counts(env, sign_in()) == [None, 1, 1]  # the pair went first; the rest stay


class Signed:
    def __init__(self, env: Env, session_id: object, token: str) -> None:
        self.env = env
        self.headers = session_cookie(token)
        self.csrf = csrf_token_for_session(env.app.state.keys, session_id)

    async def get(self, path: str) -> Any:
        return await self.env.client.get(path, headers=self.headers)

    async def post(self, path: str) -> Any:
        headers = {**self.headers, "Origin": ORIGIN, "X-CSRF-Token": self.csrf}
        return await self.env.client.post(path, headers=headers)


async def signed(env: Env) -> Signed:
    issued = await env.login(await env.user("user@example.com"))
    return Signed(env, issued.id, issued.token)


def assert_busy(response: Any) -> None:
    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(LIMIT_BUSY_RETRY_AFTER)
    assert 1 <= LIMIT_BUSY_RETRY_AFTER <= 5
    assert response.json() == {"detail": {"reason": "busy"}}
    assert response.headers["Cache-Control"] == "no-store"


class _LockTimeout(Exception):
    sqlstate = "55P03"


class _OtherError(Exception):
    sqlstate = "40P01"


async def test_a_lock_timeout_in_the_limit_is_503_and_the_route_never_runs(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    browser = await signed(env)

    async def timed_out(*_: object, **__: object) -> Any:
        raise OperationalError("INSERT planted@example.com", {}, _LockTimeout("planted"))

    together = env.app.state.limiter.begin_together
    monkeypatch.setattr(env.app.state.limiter, "begin_together", timed_out)
    assert_busy(await browser.get(SESSION))
    assert_busy(await browser.post(WRITE))
    assert env.app.state.write_calls == []  # never served: the limit is not skipped
    assert "planted" not in caplog.text
    # Not counted by the limiter, so no security-log row (its rows never exceed the budget). Counted
    # as the owner (#1006): with no user, row security hides every row and the count is always 0.
    owner = await env.scalar("SELECT id FROM users")
    count = text("SELECT count(*) FROM security_log")

    async def logged() -> int:
        async with user_session(env.factory, make_context(owner)) as db:  # type: ignore[arg-type]
            return int((await db.execute(count)).scalar_one())

    assert await logged() == 0
    monkeypatch.setattr(env.app.state.limiter, "begin_together", together)
    assert (await browser.get(SESSION)).status_code == 200
    assert await logged() == 1  # the same count sees a counted request's row: not vacuous


async def test_any_other_database_error_in_the_limit_stays_the_redacted_500(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser = await signed(env)

    async def broken(*_: object, **__: object) -> Any:
        raise OperationalError("INSERT", {}, _OtherError("deadlock"))

    monkeypatch.setattr(env.app.state.limiter, "begin_together", broken)
    response = await browser.post(WRITE)
    assert response.status_code == 500
    assert env.app.state.write_calls == []


async def test_a_real_lock_timeout_on_the_rows_is_503_not_500(
    env: Env, app_database_url: str
) -> None:
    """End to end: another transaction holds the user's rows past the role's 5 s lock timeout."""
    browser = await signed(env)
    assert (await browser.get(SESSION)).status_code == 200  # the rows exist now
    async with raw(app_database_url) as holder, holder.transaction():
        await holder.execute("SELECT 1 FROM rate_limits FOR UPDATE")
        assert_busy(await browser.post(WRITE))
    assert env.app.state.write_calls == []
    assert (await browser.post(WRITE)).status_code == 200
    assert env.app.state.write_calls == ["write"]
