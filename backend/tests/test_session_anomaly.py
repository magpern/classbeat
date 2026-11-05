"""A session seen from a new network or browser (#632, ADR 0003 amendment of 2026-11-05).

Every session keeps a client mark: the HMAC of its coarse network (``bucket_ip``) and its browser
family. A request with a different mark is recorded once (``auth_events``), clears the session's
recent authentication, MFA and step-up times (for that very request too) and flags the session in
the session list. It never signs the session out, so a phone moving between wifi and mobile data
keeps working. Fakes only: the client address comes from ``X-Forwarded-For`` behind a trusted
loopback proxy, the browser from ``User-Agent``.
"""

import ast
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID

import pytest
from fastapi import Depends
from sqlalchemy import text

from authtools import Env, running_env
from classbeat import auth
from classbeat.auth import sessions as sessions_module
from classbeat.auth.anomaly import EVENT_KIND, agent_family, client_mark
from classbeat.auth.deps import require_recent_auth
from classbeat.auth.export import account_export
from classbeat.auth.keys import Keys, hmac_digest
from classbeat.context import UserContext
from flows import PASSWORD, Browser, user_with_password

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #588: no window edge in a test

SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat"
GUARDED = "/api/test/guarded"
SECRET = b"s" * 32

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
CHROME_WINDOWS_NEXT = CHROME_WINDOWS.replace("Chrome/130", "Chrome/131")  # a browser update
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
HOME, MOBILE = "198.51.100.7", "203.0.113.40"


# --- the mark itself ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "family"),
    [
        (CHROME_WINDOWS, "chrome/windows"),
        (CHROME_WINDOWS_NEXT, "chrome/windows"),
        (FIREFOX_LINUX, "firefox/linux"),
        (SAFARI_IPHONE, "safari/ios"),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like "
            "Gecko) Version/18.1 Safari/605.1.15",
            "safari/macos",
        ),
        (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Mobile Safari/537.36",
            "chrome/android",
        ),
        (CHROME_WINDOWS + " Edg/130.0.0.0", "edge/windows"),
        (CHROME_WINDOWS + " OPR/114.0.0.0", "opera/windows"),
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, "
            "like Gecko) CriOS/130.0.0.0 Mobile/15E148 Safari/604.1",
            "chrome/ios",
        ),
        (
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36",
            "chrome/chromeos",
        ),
        ("Mozilla/5.0 HeadlessChrome/130.0.0.0 Safari/537.36 (X11; Linux x86_64)", "chrome/linux"),
        ("python-requests/2.32.3", "other/other"),
        ("curl/8.10.1", "other/other"),
        ("", "none/none"),
        (None, "none/none"),
    ],
)
def test_the_browser_family_has_no_version(agent: str | None, family: str) -> None:
    assert agent_family(agent) == family


def test_the_network_is_a_keyed_hash_of_the_coarse_address() -> None:
    keys = Keys.from_secret(SECRET)
    one = client_mark(keys, "2001:db8:1:2::10", CHROME_WINDOWS)
    # The same /64 is the same network (an IPv6 privacy address changes inside it).
    assert one.ip_hash == client_mark(keys, "2001:db8:1:2:aaaa::1", CHROME_WINDOWS).ip_hash
    assert one.ip_hash != client_mark(keys, "2001:db8:1:3::10", CHROME_WINDOWS).ip_hash
    assert client_mark(keys, HOME, None).ip_hash != client_mark(keys, MOBILE, None).ip_hash
    # The IPv4-mapped form of an address is the address.
    assert (
        client_mark(keys, f"::ffff:{HOME}", None).ip_hash == client_mark(keys, HOME, None).ip_hash
    )
    assert one.ip_hash == hmac_digest(keys.session_client, "2001:db8:1:2::/64")
    assert one.ip_hash != hmac_digest(keys.rate_limit, "2001:db8:1:2::/64")  # its own key
    assert b"2001" not in one.ip_hash
    assert "2001" not in repr(one)
    assert repr(one) == "ClientMark(agent='chrome/windows')"


# --- requests --------------------------------------------------------------------------------


class Client(Browser):
    """A browser at a client address with a ``User-Agent``; both can change between requests."""

    def __init__(self, env: Env, ip: str = HOME, agent: str = CHROME_WINDOWS) -> None:
        super().__init__(env)
        self.ip, self.agent = ip, agent

    def _cookie_header(self) -> dict[str, str]:
        return {**super()._cookie_header(), "X-Forwarded-For": self.ip, "User-Agent": self.agent}

    def move(self, ip: str | None = None, agent: str | None = None) -> "Client":
        self.ip, self.agent = ip or self.ip, agent or self.agent
        return self

    def copy(self) -> "Client":
        twin = Client(self.env, self.ip, self.agent)
        twin.cookies = dict(self.cookies)
        return twin

    async def user(self) -> Any:
        return (await self.get("/api/auth/session")).json()["user"]

    async def guarded(self) -> int:
        return (await self.get(GUARDED)).status_code


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    """An app behind a trusted loopback proxy, with a route that needs recent authentication."""
    async with running_env(app_database_url, trusted_proxies="127.0.0.1") as running:

        @running.app.get(GUARDED)
        async def guarded(  # pyright: ignore[reportUnusedFunction]
            ctx: Annotated[UserContext, Depends(require_recent_auth)],
        ) -> dict[str, bool]:
            return {"ok": True}

        yield running


async def sign_in(env: Env, ip: str = HOME, agent: str = CHROME_WINDOWS) -> Client:
    client = Client(env, ip, agent)
    await user_with_password(env)
    assert (await client.sign_in("user@example.com")).status_code == 200
    return client


async def events(env: Env) -> list[Any]:
    async with env.db() as db:
        rows = await db.execute(
            text("SELECT user_id, ip, detail FROM auth_events WHERE kind = :k ORDER BY at"),
            {"k": EVENT_KIND},
        )
        return list(rows.all())


async def session_row(env: Env) -> Any:
    async with env.db() as db:
        return (
            await db.execute(
                text(
                    "SELECT id, seen_ip_hash, seen_agent, location_changed_at, reauth_at, "
                    "stepup_at, mfa_at FROM sessions"
                )
            )
        ).one()


async def test_sign_in_stores_the_mark_and_the_same_network_is_no_move(env: Env) -> None:
    client = await sign_in(env)
    row = await session_row(env)
    assert row.seen_agent == "chrome/windows"
    assert bytes(row.seen_ip_hash) == client_mark(env.app.state.keys, HOME, None).ip_hash
    assert row.location_changed_at is None
    # A browser update on the same network changes nothing: the sign-in's recent auth holds.
    client.move(agent=CHROME_WINDOWS_NEXT)
    assert await client.guarded() == 200
    assert await events(env) == []
    assert (await session_row(env)).reauth_at is not None


async def test_a_new_network_is_recorded_and_needs_recent_auth_but_never_signs_out(
    env: Env,
) -> None:
    client = await sign_in(env)
    assert await client.guarded() == 200
    # The phone leaves the wifi: the very first request from mobile data runs without recent auth.
    response = await client.move(ip=MOBILE).get(GUARDED)
    assert response.status_code == 403
    assert response.json() == {"detail": {"reason": "reauth_required"}}
    assert auth.SESSION_COOKIE not in response.headers.get("set-cookie", "")
    # Still signed in, with the same session, for everything else.
    assert (await client.user())["email"] == "user@example.com"
    assert client.signed_in
    row = await session_row(env)
    assert row.location_changed_at is not None
    assert (row.reauth_at, row.stepup_at, row.mfa_at) == (None, None, None)
    assert bytes(row.seen_ip_hash) == client_mark(env.app.state.keys, MOBILE, None).ip_hash
    user_id = await env.scalar("SELECT id FROM users")
    [event] = await events(env)
    assert (event.user_id, event.ip) == (user_id, MOBILE)
    assert event.detail == {
        "session_id": str(row.id),
        "ip_changed": True,
        "agent_changed": False,
        "agent": "chrome/windows",
    }
    # The session list says so.
    [listed] = (await client.get("/api/auth/sessions")).json()
    assert listed["current"] is True
    assert listed["location_changed_at"] is not None


async def test_a_new_browser_family_is_a_move_too(env: Env) -> None:
    client = await sign_in(env)
    assert await client.move(agent=FIREFOX_LINUX).guarded() == 403
    [event] = await events(env)
    assert event.detail["ip_changed"] is False
    assert event.detail["agent_changed"] is True
    assert event.detail["agent"] == "firefox/linux"


async def test_alternating_networks_clear_every_time_but_write_one_event_a_minute(
    env: Env,
) -> None:
    client = await sign_in(env)
    for ip in (MOBILE, HOME, MOBILE):
        assert await client.move(ip=ip).guarded() == 403
    assert len(await events(env)) == 1
    # A minute later the next move is recorded again.
    await env.run("UPDATE sessions SET location_changed_at = now() - interval '61 seconds'")
    assert await client.move(ip=HOME).guarded() == 403
    assert len(await events(env)) == 2


async def test_re_authenticating_on_the_new_network_clears_the_flag(env: Env) -> None:
    client = await sign_in(env)
    client.move(ip=MOBILE)
    assert await client.guarded() == 403
    assert (await client.post("/api/auth/reauth", {"password": PASSWORD})).status_code == 200
    assert await client.guarded() == 200
    row = await session_row(env)
    assert row.location_changed_at is None
    assert bytes(row.seen_ip_hash) == client_mark(env.app.state.keys, MOBILE, None).ip_hash
    [listed] = (await client.get("/api/auth/sessions")).json()
    assert listed["location_changed_at"] is None
    assert len(await events(env)) == 1


async def test_an_admin_needs_totp_again_after_a_move(env: Env) -> None:
    keys = env.app.state.keys
    user_id = await env.user("admin@example.com", role="admin")
    issued = await env.login(user_id, mfa=True, mark=client_mark(keys, HOME, CHROME_WINDOWS))
    await env.run("UPDATE sessions SET stepup_at = now()")
    client = Client(env)
    client.cookies[auth.SESSION_COOKIE] = issued.token
    assert (await client.get("/api/admin/whoami")).status_code == 200
    response = await client.move(ip=MOBILE).get("/api/admin/whoami")
    assert response.status_code == 403
    assert response.json() == {"detail": {"reason": "mfa_required"}}
    assert (await client.user())["email"] == "admin@example.com"
    row = await session_row(env)
    assert (row.mfa_at, row.stepup_at) == (None, None)


async def test_parallel_requests_from_the_new_network_record_one_event(env: Env) -> None:
    client = await sign_in(env)
    client.move(ip=MOBILE)
    twins = [client.copy() for _ in range(8)]
    answers = await asyncio.gather(*(twin.guarded() for twin in twins))
    # Every one of them runs without recent auth, whichever update won.
    assert answers == [403] * 8
    assert len(await events(env)) == 1
    assert (await client.user())["email"] == "user@example.com"


async def test_a_request_that_lost_the_race_still_runs_without_recent_auth_and_writes_nothing(
    env: Env,
) -> None:
    """Deterministic form of the race above: a request that read the session before a parallel
    request stored the new mark. Its conditional update changes nothing, so it writes no second
    event, yet it reports the move, so it runs without the cleared times."""
    client = await sign_in(env)
    before = await session_row(env)
    async with env.db() as db:
        now = (await db.execute(text("SELECT now()"))).scalar_one()
    stale = SimpleNamespace(
        id=before.id,
        user_id=await env.scalar("SELECT id FROM users"),
        seen_ip_hash=before.seen_ip_hash,
        seen_agent=before.seen_agent,
        location_changed_at=None,
        now=now,
    )
    assert await client.move(ip=MOBILE).guarded() == 403  # the winner
    assert len(await events(env)) == 1
    mark = client_mark(env.app.state.keys, MOBILE, CHROME_WINDOWS)
    async with env.db() as db:
        assert await sessions_module._compare_mark(db, stale, mark, MOBILE) is True
    assert len(await events(env)) == 1


async def test_a_session_without_a_mark_takes_its_first_requests_silently(env: Env) -> None:
    """A session from before migration 0033 (``env.login`` makes one without a mark)."""
    issued = await env.login(await env.user())
    assert (await session_row(env)).seen_ip_hash is None
    client = Client(env, ip=MOBILE, agent=SAFARI_IPHONE)
    client.cookies[auth.SESSION_COOKIE] = issued.token
    assert await client.guarded() == 200  # adopted, not a move
    row = await session_row(env)
    assert row.seen_agent == "safari/ios"
    assert row.location_changed_at is None
    assert await events(env) == []
    assert await client.move(ip=HOME).guarded() == 403
    assert len(await events(env)) == 1


async def test_the_streams_check_without_a_mark_compares_nothing(env: Env) -> None:
    client = await sign_in(env)
    token = client.cookies[auth.SESSION_COOKIE]
    resolution = await auth.resolve_session(env.factory, env.settings, token)
    assert resolution.info is not None
    assert resolution.info.recent_auth
    assert await events(env) == []


async def test_the_move_is_per_session(env: Env) -> None:
    """Another session of the same user on the old network keeps its recent auth."""
    phone = await sign_in(env)
    laptop = Client(env, HOME, FIREFOX_LINUX)
    assert (await laptop.sign_in("user@example.com")).status_code == 200
    assert await phone.move(ip=MOBILE).guarded() == 403
    assert await laptop.guarded() == 200
    listed = {s["user_agent"]: s for s in (await laptop.get("/api/auth/sessions")).json()}
    assert listed[CHROME_WINDOWS]["location_changed_at"] is not None
    assert listed[FIREFOX_LINUX]["location_changed_at"] is None


async def test_the_export_has_the_family_and_the_flag_but_not_the_hash(env: Env) -> None:
    client = await sign_in(env)
    await client.move(ip=MOBILE).user()
    user_id = await env.scalar("SELECT id FROM users")
    assert isinstance(user_id, UUID)
    async with env.db() as db:
        document = await account_export(db, user_id)
    [session] = document["sessions"]
    assert session["browser_family"] == "chrome/windows"
    assert session["location_changed_at"] is not None
    assert "seen_ip_hash" not in session
    assert any(e["kind"] == EVENT_KIND for e in document["security_events"])


# --- guards ----------------------------------------------------------------------------------


def _calls(name: str) -> list[tuple[str, int, ast.Call]]:
    found: list[tuple[str, int, ast.Call]] = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called == name:
                found.append((str(path.relative_to(SRC)), node.lineno, node))
    return found


def _passes_a_mark(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "mark":
            value = keyword.value
            return not (isinstance(value, ast.Constant) and value.value is None)
    return False


@pytest.mark.parametrize("name", ["start_session", "rotate_session"])
def test_every_new_or_rotated_session_gets_the_requests_mark(name: str) -> None:
    """A sign-in path or rotation added later cannot forget the mark (it would leave the session
    unmarked until its next request, or keep the old network's mark after a re-authentication)."""
    calls = _calls(name)
    assert calls, name
    missing = [(path, line) for path, line, call in calls if not _passes_a_mark(call)]
    assert missing == []


def test_only_the_streams_check_resolves_a_session_without_a_mark() -> None:
    calls = _calls("resolve_session")
    without = sorted(
        path
        for path, _, call in calls
        if len(call.args) < 4 and not any(k.arg == "mark" for k in call.keywords)
    )
    assert without == ["jobs/events_routes.py"]
    assert len(calls) == 2  # deps.resolve_request and the stream's check


def test_the_guard_can_fail() -> None:
    bad = ast.parse("start_session(db, s, u, user_agent=a, ip=i, mark=None)").body[0]
    good = ast.parse("start_session(db, s, u, mark=client_mark(k, i, a))").body[0]
    assert isinstance(bad, ast.Expr)
    assert isinstance(bad.value, ast.Call)
    assert isinstance(good, ast.Expr)
    assert isinstance(good.value, ast.Call)
    assert not _passes_a_mark(bad.value)
    assert not _passes_a_mark(ast.parse("rotate_session(db, info)").body[0].value)  # type: ignore[attr-defined]
    assert _passes_a_mark(good.value)
