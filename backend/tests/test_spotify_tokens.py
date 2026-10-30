"""Using the tokens and refreshing them (ADR 0009 section 6; issue SC1, #176): ``spotify_call``
and ``connections.refresh_spotify`` against the fake Spotify and the real library. Never the
network.

The refresh criterion, one test each: two concurrent callers make one token request and keep
the rotated refresh token; a failed write after a rotation fails the call; the library never
contacts the token endpoint; a refresh does not invalidate a running job's ``hold``; a stale 401
after a reconnect does not flip the new connection; a 401 sent with a pre-refresh access token
does not flip a freshly refreshed connection; only the token endpoint's ``invalid_grant`` /
``invalid_client`` or a 401 on the repeat with the row's current token flips; ``forbidden``,
``not_found`` and the playlist 403s never flip."""

import asyncio
import copy
import pickle
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from mowl import spotify_web
from mowl.errors import MowlError
from sqlalchemy import text

from classbeat import connections
from classbeat.connections import ConnectionFailure, spotify_tokens
from classbeat.db import user_session
from classbeat.spotify import spotify_call
from classbeat.spotify.access import refresh_if_needed
from classbeat.spotify.http import SpotifyHttp
from spotifyfake import CLIENT_A, USER_A, USER_B
from spotifytools import Spot

me = spotify_web.me


async def rider(spot: Spot, *, expires_in: float = 3000) -> tuple[Any, str, str]:
    ctx = spot.ctx(await spot.actor("rider@example.com"))
    access, refresh = await spot.seed(ctx, expires_in=expires_in)
    return ctx, access, refresh


async def call(spot: Spot, ctx: Any, fn: Callable[[Any], Any] = me, **kw: Any) -> Any:
    return await spotify_call(spot.env.factory, spot.ring, ctx, fn, http=spot.http, **kw)


async def force_refresh(spot: Spot, ctx: Any, stale: str) -> bool:
    return await refresh_if_needed(
        spot.env.factory, spot.ring, ctx, spot.http, stale_access_token=stale
    )


async def stored(spot: Spot, ctx: Any) -> dict[str, Any]:
    opened = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    return {
        "access": opened.credentials.spotify_access_token,
        "expires_at": opened.credentials.spotify_expires_at,
        "version": opened.version,
        "generation": opened.generation,
    }


def bearer(seen: Any) -> str:
    return seen.authorization.removeprefix("Bearer ")


# --- the library gets an access token only -------------------------------------------------------


async def test_the_library_gets_the_access_token_only_and_never_reaches_the_token_endpoint(
    spot: Spot,
) -> None:
    ctx, access, _ = await rider(spot)
    opened = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    creds = opened.credentials
    assert (creds.spotify_access_token, creds.spotify_refresh_token) == (access, "")
    assert creds.spotify_refresh_enabled is False
    assert creds.spotify_max_retry_wait == 0
    assert creds.spotify_client_id == CLIENT_A
    assert access not in repr(opened)
    assert access not in repr(creds)
    with pytest.raises(TypeError):
        pickle.dumps(creds)
    with pytest.raises(TypeError):
        copy.deepcopy(creds)
    assert await call(spot, ctx) == {"id": USER_A}
    # An expired token in the library's hands is its own error: no token request from it.
    creds.spotify_expires_at = time.time() - 10
    with pytest.raises(MowlError):
        spotify_web.me(creds)
    assert "/api/token" not in spot.library.paths
    assert spot.token_requests() == []


async def test_a_refresh_before_expiry_rotates_and_stores_the_tokens(spot: Spot) -> None:
    ctx, old_access, old_refresh = await rider(spot, expires_in=30)
    before = await stored(spot, ctx)
    assert await call(spot, ctx) == {"id": USER_A}
    (token,) = spot.token_requests()
    assert token.client_id == CLIENT_A
    assert not token.client_secret
    after = await stored(spot, ctx)
    assert after["access"] != old_access
    assert after["expires_at"] > time.time() + 3000
    assert after["generation"] == before["generation"]
    assert after["version"] != before["version"]
    # The call went out with the new token; the old refresh token is dead at the fake (rotated)
    # and the stored one works.
    assert bearer(spot.fake.requests("api.spotify.com", "/v1/me")[-1]) == after["access"]
    assert old_refresh not in spot.fake.refresh
    assert await force_refresh(spot, ctx, after["access"]) is True
    assert "/api/token" not in spot.library.paths


class SlowRefresh(SpotifyHttp):
    """The token request takes a while, so every caller has read the expiring row and is
    waiting for the lock while the first one holds it."""

    def __init__(self, inner: SpotifyHttp, entered: list[int]) -> None:
        super().__init__()
        self._inner = inner
        self._entered = entered

    async def refresh(self, client_id: str, refresh_token: str) -> Any:
        self._entered.append(1)
        await asyncio.sleep(0.3)
        return await self._inner.refresh(client_id, refresh_token)


async def test_two_concurrent_refreshes_make_one_request_and_keep_the_rotated_token(
    spot: Spot,
) -> None:
    ctx, _, _ = await rider(spot, expires_in=30)
    opened = [await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")]
    entered: list[int] = []
    slow = SlowRefresh(spot.http, entered)
    # Both callers saw the token expiring before either refreshes (as two jobs that opened the
    # row at the same time): each asks for a refresh, the second waits on the row lock.
    assert all(o.credentials.spotify_expires_at - time.time() < 120 for o in opened)
    sent = await asyncio.gather(
        *(refresh_if_needed(spot.env.factory, spot.ring, ctx, slow) for _ in range(2))
    )
    assert sorted(sent) == [False, True]
    assert len(entered) == len(spot.token_requests()) == 1
    # And through spotify_call: three concurrent calls on a fresh row make no further request.
    results = await asyncio.gather(*(call(spot, ctx) for _ in range(3)))
    assert results == [{"id": USER_A}] * 3
    assert len(spot.token_requests()) == 1
    # The rotated refresh token was kept: the next refresh succeeds with it.
    current = (await stored(spot, ctx))["access"]
    assert await force_refresh(spot, ctx, current) is True
    assert len(spot.token_requests()) == 2
    assert (await spot.connection(ctx)).status == "connected"


async def test_a_failed_write_after_a_rotation_fails_the_call(
    spot: Spot, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, access, _ = await rider(spot, expires_in=30)
    before = await spot.connection(ctx)

    async def broken(*_: Any) -> bool:
        raise RuntimeError("the write failed")

    monkeypatch.setattr(spotify_tokens, "_store", broken)
    with pytest.raises(RuntimeError):
        await call(spot, ctx)
    assert len(spot.token_requests()) == 1  # Spotify rotated
    assert spot.fake.requests("api.spotify.com") == []  # no call with tokens we did not keep
    assert tuple(await spot.connection(ctx)) == tuple(before)  # rolled back, lock released
    monkeypatch.undo()
    # The rotated refresh token is lost: the next refresh is invalid_grant and flips (section 6).
    with pytest.raises(ConnectionFailure) as failed:
        await force_refresh(spot, ctx, access)
    assert failed.value.category == "invalid_grant"
    assert (await spot.connection(ctx)).status == "needs_reconnect"


async def test_a_held_row_lock_ends_in_a_retryable_failure_without_a_request(
    spot: Spot, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _, _ = await rider(spot, expires_in=30)
    monkeypatch.setattr(spotify_tokens, "LOCK_TIMEOUT", "200ms")
    locked, release = asyncio.Event(), asyncio.Event()

    async def holder() -> None:
        async with user_session(spot.env.factory, ctx) as db:
            await db.execute(
                text("SELECT id FROM connections WHERE provider = 'spotify' FOR UPDATE")
            )
            locked.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await locked.wait()
    try:
        with pytest.raises(ConnectionFailure) as failed:
            await call(spot, ctx)
    finally:
        release.set()
        await task
    assert failed.value.category == "upstream_unavailable"
    assert spot.token_requests() == []


async def test_a_refresh_does_not_invalidate_a_running_jobs_hold(spot: Spot) -> None:
    ctx, access, _ = await rider(spot)
    opened = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    assert await force_refresh(spot, ctx, access) is True
    async with user_session(spot.env.factory, ctx) as db:
        assert await connections.hold(db, ctx, opened) is True


# --- what flips, and what never does -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "category"),
    [("invalid_grant", "invalid_grant"), ("invalid_client", "app_misconfigured")],
)
async def test_the_token_endpoints_refusal_flips_once_and_keeps_the_tokens(
    spot: Spot, kind: str, category: str
) -> None:
    ctx, _, _ = await rider(spot, expires_in=30)
    before = await spot.connection(ctx)
    spot.fake.inject(kind, path="/api/token")
    with pytest.raises(ConnectionFailure) as failed:
        await call(spot, ctx)
    assert failed.value.category == category
    after = await spot.connection(ctx)
    assert (after.status, after.last_error) == ("needs_reconnect", category)
    assert (after.secret, after.wrapped_dek, after.generation) == (
        before.secret, before.wrapped_dek, before.generation
    )  # fmt: skip
    with pytest.raises(ConnectionFailure) as again:
        await call(spot, ctx)
    assert again.value.category == "not_connected"
    assert len(spot.token_requests()) == 1


@pytest.mark.parametrize(
    ("options", "category"),
    [
        ({"kind": "status", "status": 503}, "upstream_unavailable"),
        ({"kind": "rate_limited", "retry_after": 30}, "rate_limited"),
        ({"kind": "refuse"}, "upstream_unavailable"),
    ],
)
async def test_other_refresh_failures_never_flip(
    spot: Spot, options: dict[str, Any], category: str
) -> None:
    ctx, _, _ = await rider(spot, expires_in=30)
    spot.fake.inject(path="/api/token", **options)
    with pytest.raises(ConnectionFailure) as failed:
        await call(spot, ctx)
    assert failed.value.category == category
    assert (await spot.connection(ctx)).status == "connected"


async def test_a_401_on_the_repeat_with_the_current_token_flips(spot: Spot) -> None:
    ctx, _, _ = await rider(spot)
    spot.fake.inject("status", status=401, path="/v1/me", times=2)
    with pytest.raises(ConnectionFailure) as failed:
        await call(spot, ctx)
    assert failed.value.category == "invalid_grant"
    assert len(spot.token_requests()) == 1  # the 401 forced one refresh
    sent = spot.fake.requests("api.spotify.com", "/v1/me")
    assert len(sent) == 2
    assert bearer(sent[0]) != bearer(sent[1])
    row = await spot.connection(ctx)
    assert (row.status, row.last_error) == ("needs_reconnect", "invalid_grant")


async def test_a_401_answered_once_is_repeated_with_the_refreshed_token(spot: Spot) -> None:
    ctx, _, _ = await rider(spot)
    spot.fake.inject("status", status=401, path="/v1/me")
    assert await call(spot, ctx) == {"id": USER_A}
    assert len(spot.token_requests()) == 1
    row = await spot.connection(ctx)
    assert (row.status, row.last_error) == ("connected", None)


async def test_a_write_is_never_repeated_and_its_401_does_not_flip(spot: Spot) -> None:
    ctx, _, _ = await rider(spot)
    spot.fake.inject("status", status=401, path="/v1/me")
    with pytest.raises(ConnectionFailure) as failed:
        await call(spot, ctx, write=True)
    assert (failed.value.category, failed.value.not_applied) == ("upstream_unavailable", True)
    assert len(spot.fake.requests("api.spotify.com", "/v1/me")) == 1  # not repeated
    assert len(spot.token_requests()) == 1  # but the refresh ran
    assert (await spot.connection(ctx)).status == "connected"


def _blocking(first: threading.Event, go: threading.Event) -> Callable[[Any], Any]:
    """A call whose first run waits (holding the credentials it opened) until ``go``."""
    calls = [0]

    def fn(creds: Any) -> Any:
        calls[0] += 1
        if calls[0] == 1:
            first.set()
            assert go.wait(10)
        return spotify_web.me(creds)

    return fn


async def _run_blocked(spot: Spot, ctx: Any, meanwhile: Callable[[], Any]) -> Any:
    first, go = threading.Event(), threading.Event()
    task = asyncio.create_task(call(spot, ctx, _blocking(first, go)))
    await asyncio.to_thread(first.wait, 10)
    try:
        await meanwhile()
    finally:
        go.set()
    return await task


async def test_a_401_with_a_pre_refresh_token_does_not_flip_the_refreshed_connection(
    spot: Spot,
) -> None:
    ctx, old_access, _ = await rider(spot)

    async def refresh_meanwhile() -> None:
        assert await force_refresh(spot, ctx, old_access) is True
        spot.fake.expire(old_access)  # the old token now answers 401

    assert await _run_blocked(spot, ctx, refresh_meanwhile) == {"id": USER_A}
    # The 401 caused no second refresh (the row already holds another token), only the repeat.
    assert len(spot.token_requests()) == 1
    sent = spot.fake.requests("api.spotify.com", "/v1/me")
    assert [s.status for s in sent] == [401, 200]
    row = await spot.connection(ctx)
    assert (row.status, row.last_error) == ("connected", None)


async def test_a_stale_401_after_a_reconnect_does_not_flip_the_new_connection(
    spot: Spot,
) -> None:
    ctx, old_access, _ = await rider(spot)

    async def reconnect_meanwhile() -> None:
        await spot.seed(ctx)  # the user's reconnect: generation + 1
        spot.fake.expire(old_access)

    assert await _run_blocked(spot, ctx, reconnect_meanwhile) == {"id": USER_A}
    assert spot.token_requests() == []
    row = await spot.connection(ctx)
    assert (row.status, row.generation) == ("connected", 2)


async def test_a_flip_after_another_refresh_writes_nothing(spot: Spot) -> None:
    """The repeat's flip compares the ``wrapped_dek`` it opened: a refresh after that wins."""
    ctx, access, _ = await rider(spot)
    repeat = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    assert await force_refresh(spot, ctx, access) is True
    assert await connections.mark_needs_reconnect(
        spot.env.factory, ctx, repeat, "invalid_grant"
    ) is False  # fmt: skip
    assert (await spot.connection(ctx)).status == "connected"


async def test_forbidden_not_found_and_playlist_403s_never_flip(spot: Spot) -> None:
    ctx, _, _ = await rider(spot)
    other = spot.fake.add_playlist(USER_B, "Not yours", [])
    spot.fake.foreign = "visible"
    cases: list[tuple[Callable[[Any], Any], str]] = [
        (lambda c: spotify_web.playlist_items(c, other.id), "forbidden"),
        (lambda c: spotify_web.get_playlist(c, "0" * 22), "not_found"),
    ]
    for fn, category in cases:
        with pytest.raises(ConnectionFailure) as failed:
            await call(spot, ctx, fn)
        assert failed.value.category == category
        row = await spot.connection(ctx)
        assert (row.status, row.last_error) == ("connected", category)
    spot.fake.inject("status", status=403, message="Forbidden", path="/v1/me")
    with pytest.raises(ConnectionFailure) as failed:
        await call(spot, ctx)
    assert failed.value.category == "forbidden"
    assert (await spot.connection(ctx)).status == "connected"
    assert spot.token_requests() == []
    opened = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    with pytest.raises(ValueError, match="invalid_grant"):
        await connections.mark_needs_reconnect(spot.env.factory, ctx, opened, "forbidden")


async def test_tokens_never_appear_in_reprs(spot: Spot) -> None:
    ctx, access, refresh = await rider(spot)
    answer = connections.RefreshedTokens(access, refresh, 3600)
    assert access not in repr(answer)
    assert refresh not in str(answer)
    with pytest.raises(TypeError):
        pickle.dumps(answer)
    opened = await connections.open_connection(spot.env.factory, spot.ring, ctx, "spotify")
    assert access not in repr(opened)
