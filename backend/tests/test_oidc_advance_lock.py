"""The player sign-in code step against the revert and the reset that burn codes (#1294), and
against a user disabled or deleted while it waits (#1312).

``advance`` (``/oidc/authorize/continue`` and the consent POST) takes the user row lock first and
re-checks its session under it before ``issue_code`` (ADR 0003 "Requests in flight"). Each pair
is run in both orders, deterministically (one side parked inside its transaction, the other
waiting for Postgres to report it blocked), and must end without a deadlock and without an
unused code once the rival committed:

- *advance holds the lock:* its code commits first, and the waiting revert or reset burns it, so
  the player's redemption is the uniform ``invalid_grant``;
- *the rival holds the lock:* advance waits, then finds its session gone and answers as for a
  signed-out caller (the sign-in redirect at continue, ``request_expired`` at consent). No code
  is inserted after the burn and the request row stays.

An admin disable (``disable_account`` deletes the user's sessions) or a delete
(``delete_account``) that commits while advance waits ends the same way, with the same two
answers: a deleted user no longer gets the ``access_denied`` redirect. Those rivals are the real
admin functions run under the lock the admin routes take first (``get_account(lock=True)``), not
a parked route.

Mutations that fail this module (#1294, #1312): *advance takes no user lock* fails every case
(the rival or advance never waits); *advance does not re-check the session* fails every
``rival-holds`` case (a code is inserted after the burn) and both disabled cases; *``SessionGone``
is not handled at continue* (it falls to the expired page) fails the continue cases of the
revert, the reset, the disabled and the deleted user. A deleted user is signed out by either
check (``lock_user`` finds no row, and the cascade took its sessions), so only removing both
fails the deleted cases. Every wait is bounded, so a regression ends in a red test, not a stuck run.
"""

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from authtools import Env
from classbeat import auth
from classbeat.auth import email_revert, reset_routes
from classbeat.auth.oidc import advance
from flows import Browser, user_with_password
from oidctools import REDIRECT_URI, FakePlayer, flow_cookie_of, oidc_env, query_of
from smtpfake import SmtpFake
from test_auth_email_revert import NEW_PASSWORD, OLD, changed, revert, revert_token
from test_auth_reset_races import own_client, parked, until_it_waits

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #1012

BOUND = 20  # seconds: far above a real run, so only a hang or a deadlock reaches it
Entry = Literal["continue", "consent"]


@pytest.fixture
async def penv(
    clean_auth: None, app_database_url: str, kek_dir: Path, smtp: SmtpFake
) -> AsyncIterator[Env]:
    async with oidc_env(
        app_database_url, kek_dir, smtp_host="127.0.0.1", smtp_port=smtp.port, smtp_tls="none"
    ) as running:
        yield running


@dataclass(frozen=True)
class Rival:
    owner: UUID
    send: Callable[[], Awaitable[httpx.Response]]
    module: ModuleType  # where it is parked while it holds the user row lock


async def a_revert(env: Env, smtp: SmtpFake) -> Rival:
    """The changer moved the account; the owner opens the revert link of the notice."""
    await changed(env, smtp)
    link = revert_token(smtp)
    owner: UUID = await env.scalar("SELECT id FROM users")  # type: ignore[assignment]

    async def send() -> httpx.Response:
        async with own_client(env) as other:
            return await revert(link, other)

    return Rival(owner, send, email_revert)


async def a_reset(env: Env, smtp: SmtpFake) -> Rival:
    """The owner completes a reset; asked for before anything is parked (same module)."""
    owner = await user_with_password(env, OLD)
    asked = await Browser(env).post("/api/auth/password/reset/request", {"email": OLD})
    assert asked.status_code == 202
    token = smtp.to(OLD)[-1].token()

    async def send() -> httpx.Response:
        async with own_client(env) as other:
            return await Browser(other).post(
                "/api/auth/password/reset/complete",
                {"token": token, "password": NEW_PASSWORD, "keep_methods": []},
            )

    return Rival(owner, send, reset_routes)


async def pending_request(
    env: Env, session: str, entry: Entry
) -> tuple[FakePlayer, Callable[[], Awaitable[httpx.Response]]]:
    """A consented player that starts a new sign-in, and the step that issues its code."""
    first = FakePlayer(env)
    assert (await first.token(await first.code_for(session))).status_code == 200  # consented
    player = FakePlayer(env)

    async def send() -> httpx.Response:
        async with own_client(env) as other:
            own = dataclasses.replace(player, env=other)  # the same verifier, state and nonce
            started = await own.authorize()
            flow = flow_cookie_of(started)
            location = started.headers["location"]
            if entry == "continue":
                return await own.follow(location, flow, session)
            return await own.consent(query_of(location)["r"], flow, session)

    return player, send


def code_of(answer: httpx.Response, entry: Entry) -> str:
    if entry == "continue":
        assert answer.status_code == 302, answer.text
        location = answer.headers["location"]
    else:
        assert answer.status_code == 200, answer.text
        location = answer.json()["redirect"]
    assert location.startswith(REDIRECT_URI + "?"), location
    return query_of(location)["code"]


def signed_out(answer: httpx.Response, entry: Entry) -> bool:
    if entry == "continue":
        return answer.status_code == 302 and answer.headers["location"].startswith(
            "/signin?return_to=%2Foidc%2Fauthorize%2Fcontinue"
        )
    return (answer.status_code, answer.json()) == (
        400,
        {"detail": {"reason": "request_expired"}},
    )


CASES = [
    pytest.param(a_revert, "continue", True, id="revert-continue-advance-holds"),
    pytest.param(a_revert, "continue", False, id="revert-continue-rival-holds"),
    pytest.param(a_revert, "consent", True, id="revert-consent-advance-holds"),
    pytest.param(a_revert, "consent", False, id="revert-consent-rival-holds"),
    pytest.param(a_reset, "continue", True, id="reset-continue-advance-holds"),
    pytest.param(a_reset, "continue", False, id="reset-continue-rival-holds"),
]


@pytest.mark.parametrize(("prepare", "entry", "advance_holds"), CASES)
async def test_no_code_outlives_a_revert_or_reset_in_either_order(
    penv: Env,
    smtp: SmtpFake,
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[Env, SmtpFake], Awaitable[Rival]],
    entry: Entry,
    advance_holds: bool,
) -> None:
    rival = await prepare(penv, smtp)
    session = (await penv.login(rival.owner)).token
    player, issue = await pending_request(penv, session, entry)
    if advance_holds:  # `person` is advance's first read, with or without the lock before it
        inside, release = parked(monkeypatch, advance, "person")
    else:
        inside, release = parked(monkeypatch, rival.module, "lock_user")
    start_issue = lambda: asyncio.ensure_future(issue())  # noqa: E731
    start_rival = lambda: asyncio.ensure_future(rival.send())  # noqa: E731
    first = (start_issue if advance_holds else start_rival)()
    tasks = [first]
    try:
        await asyncio.wait_for(inside.wait(), BOUND)  # `first` holds the user row lock
        second = (start_rival if advance_holds else start_issue)()
        tasks.append(second)
        await asyncio.wait_for(until_it_waits(penv, second), BOUND)  # `second` is blocked
    except BaseException:
        for task in tasks:
            task.cancel()
        raise
    finally:
        release.set()
    answers = await asyncio.wait_for(asyncio.gather(first, second), BOUND)
    issued, done = answers if advance_holds else answers[::-1]

    assert done.status_code == 200, done.text
    unused = "SELECT count(*) FROM oidc_codes WHERE used_at IS NULL"
    if advance_holds:
        code = code_of(issued, entry)
        assert await penv.scalar(unused) == 0  # burnt by the rival that waited
        refused = await player.token(code)  # its own verifier: only the burn refuses it
        assert (refused.status_code, refused.json()) == (400, {"error": "invalid_grant"})
    else:
        assert await penv.scalar(unused) == 0  # none inserted after the burn
        assert signed_out(issued, entry), (issued.status_code, issued.text)
        assert await penv.scalar("SELECT count(*) FROM oidc_requests") == 1  # not taken


async def disable(db: AsyncSession, user_id: UUID) -> None:
    await auth.disable_account(db, user_id)  # also deletes the user's sessions


async def delete(db: AsyncSession, user_id: UUID) -> None:
    assert await auth.delete_account(db, user_id)


@pytest.mark.parametrize("end", [disable, delete], ids=["disabled", "deleted"])
@pytest.mark.parametrize("entry", ["continue", "consent"])
async def test_a_user_disabled_or_deleted_while_advance_waits_is_signed_out(
    penv: Env, end: Callable[[AsyncSession, UUID], Awaitable[None]], entry: Entry
) -> None:
    """The admin (or the account's own delete) holds the user row lock, advance waits for it,
    then the account is disabled or deleted and committed. Advance finds its session gone (a
    deleted row is the same: ``lock_user`` is ``None``) and answers as for a signed-out caller,
    not with a code and, for a deleted user, not with the ``access_denied`` redirect."""
    owner = await penv.user()
    session = (await penv.login(owner)).token
    _, issue = await pending_request(penv, session, entry)
    async with penv.db() as db:
        await auth.get_account(db, owner, lock=True)  # the admin routes' first statement
        waiting = asyncio.ensure_future(issue())
        try:
            await asyncio.wait_for(until_it_waits(penv, waiting), BOUND)
            await end(db, owner)
        except BaseException:
            waiting.cancel()
            raise
    answer = await asyncio.wait_for(waiting, BOUND)  # the commit above released the lock

    assert signed_out(answer, entry), (answer.status_code, answer.text)
    assert "access_denied" not in answer.text + answer.headers.get("location", "")
    assert await penv.scalar("SELECT count(*) FROM oidc_codes WHERE used_at IS NULL") == 0
    assert await penv.scalar("SELECT count(*) FROM oidc_requests") == 1  # not taken
