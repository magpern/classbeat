"""The lock order of the email revert against the token paths that race it (#1292, #1265 review).

The revert, the reset completion and the email confirmation all take the user row lock **before**
they consume their token (ADR 0003 "Requests in flight", ADR 0019 section 6). Each pair is run in
both orders, deterministically (one side parked inside its transaction, the other waiting for
Postgres to report it blocked), and must end without a deadlock: one answer 200, the other the
uniform 400 ``invalid_token``, and the account in one of the two consistent states.

Two mutations brought a deadlock (a 500) back and passed the tests of #1284; this module fails for
both of them, each in a different order of the confirmation pair:

- *the revert consumes its token before locking the user* fails ``rival_holds_the_lock``: the
  confirmation holds the lock and wants to delete the revert token the revert has already taken;
- *the confirmation takes no lock* fails ``revert_holds_the_lock``: it consumes its change token,
  then waits for the user row the revert holds, and the revert wants that token row.

Every wait is bounded, so a mutation ends in a red test, not a stuck run.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType

import httpx
import pytest

from authtools import Env
from classbeat.auth import email_revert, email_routes, reset_routes
from flows import Browser, reason
from smtpfake import SmtpFake
from test_auth_email_revert import NEW, NEW_PASSWORD, changed, revert, revert_token
from test_auth_email_revert_races import ASK, COMPLETE, locked_out, reset_links_to
from test_auth_reset_races import own_client, parked, until_it_waits

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #1012

THIRD = "third@example.org"
BOUND = 20  # seconds: far above a real run, so only a hang or a deadlock reaches it


@dataclass(frozen=True)
class Prepared:
    send: Callable[[], Awaitable[httpx.Response]]
    module: ModuleType  # where the rival is parked while it holds the user row lock
    name: str


async def confirmation(env: Env, smtp: SmtpFake, changer: Browser) -> Prepared:
    """The changer asked for a second change, NEW -> THIRD, and now confirms it."""
    asked = await changer.post("/api/auth/email/change", {"new_email": THIRD})
    assert asked.status_code == 202
    [token] = [m.token() for m in smtp.to(THIRD) if "/email-change#token=" in m.text]

    async def send() -> httpx.Response:
        return await changer.post("/api/auth/email/confirm", {"token": token})

    return Prepared(send, email_routes, "revoke_user_sessions")  # after its UPDATE users


async def reset_completion(env: Env, smtp: SmtpFake, changer: Browser) -> Prepared:
    """A reset the changer asked for at their address, completed through their mailbox."""
    assert (await Browser(env).post(ASK, {"email": NEW})).status_code == 202
    [token] = reset_links_to(smtp, NEW)

    async def send() -> httpx.Response:
        async with own_client(env) as client:
            return await Browser(client).post(
                COMPLETE, {"token": token, "password": NEW_PASSWORD, "keep_methods": []}
            )

    return Prepared(send, reset_routes, "lock_user")


@dataclass(frozen=True)
class Outcome:
    revert: int
    rival: int
    moved: bool  # the rival's change stays (the confirmation committed first)


INVALID = "invalid_token"

CASES = [
    pytest.param(confirmation, True, Outcome(200, 400, False), id="confirmation-revert-holds"),
    pytest.param(confirmation, False, Outcome(400, 200, True), id="confirmation-rival-holds"),
    pytest.param(
        reset_completion, True, Outcome(200, 400, False), id="reset-completion-revert-holds"
    ),
    pytest.param(
        reset_completion, False, Outcome(200, 200, False), id="reset-completion-rival-holds"
    ),
]


@pytest.mark.parametrize(("prepare", "revert_holds", "expected"), CASES)
async def test_the_revert_and_a_token_path_never_deadlock_in_either_order(
    menv: Env,
    smtp: SmtpFake,
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[Env, SmtpFake, Browser], Awaitable[Prepared]],
    revert_holds: bool,
    expected: Outcome,
) -> None:
    changer = await changed(menv, smtp)
    revert_link = revert_token(smtp)
    rival = await prepare(menv, smtp, changer)
    if revert_holds:
        inside, release = parked(monkeypatch, email_revert, "lock_user")
    else:
        inside, release = parked(monkeypatch, rival.module, rival.name)
    async with own_client(menv) as other:
        start_revert = lambda: asyncio.ensure_future(revert(revert_link, other))  # noqa: E731
        start_rival = lambda: asyncio.ensure_future(rival.send())  # noqa: E731
        first = (start_revert if revert_holds else start_rival)()
        tasks = [first]
        try:
            await asyncio.wait_for(inside.wait(), BOUND)  # `first` holds the user row lock
            second = (start_rival if revert_holds else start_revert)()
            tasks.append(second)
            await asyncio.wait_for(until_it_waits(menv, second), BOUND)  # `second` is blocked
        except BaseException:
            for task in tasks:
                task.cancel()
            raise
        finally:
            release.set()
        answers = await asyncio.wait_for(asyncio.gather(first, second), BOUND)
    done_revert, done_rival = answers if revert_holds else answers[::-1]
    assert (done_revert.status_code, done_rival.status_code) == (expected.revert, expected.rival)
    for answer in (done_revert, done_rival):
        if answer.status_code == 400:
            assert reason(answer) == INVALID
    if expected.moved:
        assert await menv.scalar("SELECT email FROM users") == THIRD
        assert await menv.scalar("SELECT count(*) FROM sessions") == 1  # the changer's, kept
    else:
        await locked_out(menv)
    # No row left behind that could be used twice: at most the one token of the last step.
    assert await menv.scalar("SELECT count(*) FROM auth_tokens WHERE used_at IS NULL") == 1
