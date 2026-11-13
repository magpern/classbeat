"""Sign-in and rotation from a request without a peer address (#1023, ADR 0003 amendment of
2026-11-05).

``resolve_request`` already treats such a request as peerless (#926). The paths that write a
session (password, passkey and OAuth sign-in; password, passkey, OAuth and TOTP re-authentication;
MFA verification and step-up) get its mark from ``client_mark``, which is ``PEERLESS`` there, and
``start_session`` and ``rotate_session`` then store no mark, no address and no proof: a sign-in
starts without ``reauth_at``, ``mfa_at`` and ``stepup_at``, and a rotation keeps the row as it was.
A session row is never marked with the word ``unknown``. Fakes only: the peer is removed from the
ASGI scope, as uvicorn hands it over when ``getpeername()`` failed.
"""

import ipaddress
from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import text

from authtools import ORIGIN, Env, running_env
from classbeat import auth
from classbeat.auth import totp
from classbeat.auth.anomaly import PEERLESS, ClientMark, client_mark
from classbeat.auth.clientip import UNKNOWN_ADDRESS, bucket_ip, client_ip
from classbeat.auth.deps import require_recent_auth
from classbeat.auth.keys import Keys, hmac_digest
from classbeat.context import UserContext
from flows import PASSWORD, Browser, user_with_password
from mfatools import Actor, admin_with_totp, step_of

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

GUARDED = "/api/test/guarded"
ADMIN = "/api/admin/whoami"
AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
HOME = "198.51.100.7"
PROXY = "127.0.0.1"  # the test transport's peer, trusted as the proxy here


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    """An app behind a trusted loopback proxy, with a route that needs recent authentication."""
    async with running_env(app_database_url, trusted_proxies=PROXY) as running:

        @running.app.get(GUARDED)
        async def guarded(  # pyright: ignore[reportUnusedFunction]
            ctx: Annotated[UserContext, Depends(require_recent_auth)],
        ) -> dict[str, bool]:
            return {"ok": True}

        yield running


def peerless_client(env: Env) -> httpx.AsyncClient:
    """A client whose every request reaches the app with no peer (``scope["client"]`` is
    ``None``): what uvicorn hands over for a connection the client reset (#926)."""

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await env.app({**scope, "client": None}, receive, send)

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


class At(Browser):
    """A browser that sends ``X-Forwarded-For`` (through the trusted proxy) and a ``User-Agent``;
    with ``peerless`` its requests have no peer at all."""

    def __init__(self, env: Env, forwarded: str = HOME) -> None:
        super().__init__(env)
        self.forwarded, self.peerless = forwarded, False

    def _cookie_header(self) -> dict[str, str]:
        return {**super()._cookie_header(), "X-Forwarded-For": self.forwarded, "User-Agent": AGENT}

    async def request(
        self, method: str, path: str, json: Any = None, *, csrf: str | None = None
    ) -> httpx.Response:
        if not self.peerless:
            return await super().request(method, path, json, csrf=csrf)
        token = csrf if csrf is not None else await self.csrf()
        headers = {"Origin": ORIGIN, "X-CSRF-Token": token, **self._cookie_header()}
        async with peerless_client(self.env) as raw:
            response = await raw.request(method, path, json=json, headers=headers)
        self._store(response)
        return response

    async def guarded(self) -> int:
        return (await self.get(GUARDED)).status_code


async def session_row(env: Env) -> Any:
    async with env.db() as db:
        return (
            await db.execute(
                text(
                    "SELECT seen_ip_hash, seen_agent, ip, user_agent, location_changed_at, "
                    "reauth_at, mfa_at, stepup_at, expires_at FROM sessions"
                )
            )
        ).one()


async def location_events(env: Env) -> int:
    count = await env.scalar(
        "SELECT count(*) FROM auth_events WHERE kind = 'session_location_changed'"
    )
    assert isinstance(count, int)
    return count


def mark_of(env: Env, ip: str) -> ClientMark:
    mark = client_mark(env.app.state.keys, ip, AGENT)
    assert isinstance(mark, ClientMark)
    return mark


# --- the mark ----------------------------------------------------------------------------------


def test_only_a_missing_peer_has_the_peerless_mark() -> None:
    keys = Keys.from_secret(b"s" * 32)
    proxies = [ipaddress.ip_network(PROXY)]
    assert client_ip(None, [HOME], proxies) == UNKNOWN_ADDRESS
    assert client_mark(keys, UNKNOWN_ADDRESS, AGENT) is PEERLESS
    # A forwarded "unknown" ends the walk at the trusted proxy, whose own address is the client's.
    forwarded = client_ip(PROXY, ["unknown"], proxies)
    assert forwarded == PROXY
    assert isinstance(client_mark(keys, forwarded, AGENT), ClientMark)
    assert isinstance(client_mark(keys, HOME, None), ClientMark)


# --- start_session and rotate_session -------------------------------------------------------------


async def test_a_peerless_start_stores_no_mark_no_address_and_no_proof(env: Env) -> None:
    """Even replacing the user's own valid session with every time set, and with ``mfa``: the
    row's first request with an address adopts its mark silently, so nothing may hold until then.
    The absolute limit is still inherited (it only shortens)."""
    user_id = await env.user()
    old = await env.login(user_id, mfa=True, mark=mark_of(env, HOME))
    await env.run("UPDATE sessions SET stepup_at = now(), expires_at = now() + interval '2 days'")
    before = await session_row(env)
    async with env.db() as db:
        await auth.start_session(
            db,
            env.settings,
            user_id,
            user_agent=AGENT,
            ip=UNKNOWN_ADDRESS,
            replacing_token=old.token,
            mfa=True,
            mark=PEERLESS,
        )
    row = await session_row(env)
    assert (row.seen_ip_hash, row.seen_agent, row.ip) == (None, None, None)
    assert (row.reauth_at, row.mfa_at, row.stepup_at) == (None, None, None)
    assert row.expires_at == before.expires_at


async def test_a_peerless_rotation_keeps_the_row_and_proves_nothing(env: Env) -> None:
    user_id = await env.user()
    issued = await env.login(user_id, mark=mark_of(env, HOME))
    await env.run(
        "UPDATE sessions SET reauth_at = now() - interval '1 hour', mfa_at = NULL, "
        "stepup_at = NULL, location_changed_at = now()"
    )
    before = await session_row(env)
    info = (await auth.resolve_session(env.factory, env.settings, issued.token)).info
    assert info is not None
    async with env.db() as db:
        rotated = await auth.rotate_session(
            db,
            info,
            reauth=True,
            mfa=True,
            stepup=True,
            user_agent="curl/8",
            ip=UNKNOWN_ADDRESS,
            mark=PEERLESS,
        )
    assert rotated.token != issued.token
    after = await session_row(env)
    assert bytes(after.seen_ip_hash) == bytes(before.seen_ip_hash)
    assert tuple(after)[1:] == tuple(before)[1:]  # agent, address, flag, times: as they were


# --- the routes -------------------------------------------------------------------------------


async def test_a_peerless_password_sign_in_starts_unmarked_and_unproven(env: Env) -> None:
    """``POST /api/auth/login`` with no peer: signed in, but the row has no mark, no address and
    no ``reauth_at``. The browser's next request with its address adopts its mark without a move
    and still needs a re-authentication for the guarded actions."""
    await user_with_password(env)
    browser = At(env)
    browser.peerless = True
    assert (await browser.sign_in("user@example.com")).status_code == 200
    row = await session_row(env)
    assert (row.seen_ip_hash, row.seen_agent, row.ip, row.reauth_at) == (None, None, None, None)
    browser.peerless = False
    assert await browser.guarded() == 403
    row = await session_row(env)
    assert bytes(row.seen_ip_hash) == mark_of(env, HOME).ip_hash
    assert row.location_changed_at is None
    assert await location_events(env) == 0
    assert (await browser.post("/api/auth/reauth", {"password": PASSWORD})).status_code == 200
    assert await browser.guarded() == 200


async def test_a_sign_in_with_a_forwarded_unknown_is_marked_like_any_other(env: Env) -> None:
    """``X-Forwarded-For: unknown`` from the trusted proxy is not peerless: the proxy's own
    address is the client's, and the sign-in stores its mark, its address and ``reauth_at``."""
    await user_with_password(env)
    browser = At(env, forwarded="unknown")
    assert (await browser.sign_in("user@example.com")).status_code == 200
    row = await session_row(env)
    assert bytes(row.seen_ip_hash) == mark_of(env, PROXY).ip_hash
    assert (row.seen_agent, row.ip) == ("firefox/linux", PROXY)
    assert row.reauth_at is not None
    assert await browser.guarded() == 200


async def test_a_peerless_password_reauth_rotates_but_keeps_the_row(env: Env) -> None:
    """``POST /api/auth/reauth`` with no peer: the password is right and the token rotates, but
    the row keeps its mark and its "New location" flag and gets no ``reauth_at``."""
    await user_with_password(env)
    browser = At(env)
    assert (await browser.sign_in("user@example.com")).status_code == 200
    await env.run("UPDATE sessions SET reauth_at = NULL, location_changed_at = now()")
    before = await session_row(env)
    token = browser.cookies[auth.SESSION_COOKIE]
    browser.peerless = True
    assert (await browser.post("/api/auth/reauth", {"password": PASSWORD})).status_code == 200
    assert browser.cookies[auth.SESSION_COOKIE] != token
    after = await session_row(env)
    assert bytes(after.seen_ip_hash) == bytes(before.seen_ip_hash)
    assert tuple(after)[1:] == tuple(before)[1:]
    browser.peerless = False
    assert await browser.guarded() == 403


async def test_a_peerless_mfa_verification_sets_no_mfa_time(
    env: Env, make_actor: Any, clock: list[float]
) -> None:
    """``POST /api/auth/mfa/verify`` with no peer: the code is right and the session rotates, but
    ``mfa_at`` and ``stepup_at`` stay unset, so the admin role still needs TOTP."""
    _, secret, _, user_id = await admin_with_totp(env, make_actor)
    issued = await env.login(user_id, mark=mark_of(env, PROXY))
    clock[0] += 60
    async with peerless_client(env) as raw:
        actor = Actor(env, issued.token, raw)
        verified = await actor.post(
            "/api/auth/mfa/verify", {"code": totp.code_for_step(secret, step_of(clock[0]))}
        )
    assert verified.status_code == 200, verified.text
    assert actor.token != issued.token
    row = await env.row(
        "SELECT seen_ip_hash, ip, mfa_at, stepup_at FROM sessions ORDER BY created_at DESC LIMIT 1"
    )
    assert bytes(row.seen_ip_hash) == mark_of(env, PROXY).ip_hash
    assert (row.ip, row.mfa_at, row.stepup_at) == ("203.0.113.5", None, None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app), base_url=ORIGIN
    ) as addressed:
        whoami = await Actor(env, actor.token, addressed).get(ADMIN)
    assert whoami.json()["detail"]["reason"] == "mfa_required"


async def test_no_session_is_ever_marked_with_the_word_unknown(env: Env) -> None:
    """The mark of the word ``unknown`` (what the paths stored before #1023) is in no row after a
    peerless sign-in and a peerless re-authentication."""
    await user_with_password(env)
    browser = At(env)
    browser.peerless = True
    assert (await browser.sign_in("user@example.com")).status_code == 200
    assert (await browser.post("/api/auth/reauth", {"password": PASSWORD})).status_code == 200
    keys: Keys = env.app.state.keys
    unknown_hash = hmac_digest(keys.session_client, bucket_ip(UNKNOWN_ADDRESS))
    stored = await env.scalar(
        "SELECT count(*) FROM sessions WHERE seen_ip_hash = :h OR ip = :ip",
        h=unknown_hash,
        ip=UNKNOWN_ADDRESS,
    )
    assert stored == 0
