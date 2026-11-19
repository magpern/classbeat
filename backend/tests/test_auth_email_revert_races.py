"""An email revert racing the changer's requests in flight (#1265 review, ADR 0019 section 6).

Every path that writes a credential, issues a reset token or consumes a token of the user takes
the user row lock first, and rechecks under it what it relied on (the session, the address).
Deterministic, as in ``test_auth_reset_races``: one side is parked inside its request (an
``asyncio.Event``), and the other runs to its end or until Postgres reports it waiting for a lock.
"""

import asyncio
from typing import Any

import pytest

from authtools import Env
from classbeat.auth import email_revert, reset_routes
from flows import Browser, reason
from smtpfake import SmtpFake
from test_auth_email_revert import NEW, NEW_PASSWORD, OLD, changed, revert, revert_token
from test_auth_reset_races import own_client, parked, until_it_waits

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #1012

CHANGE = "/api/auth/password/change"
ASK = "/api/auth/password/reset/request"
COMPLETE = "/api/auth/password/reset/complete"


async def locked_out(env: Env) -> None:
    """The restored account has no password, and the changer's new one signs in nowhere."""
    user = await env.row("SELECT email, password_hash FROM users")
    assert (user.email, user.password_hash) == (OLD, None)
    assert (await Browser(env).sign_in(OLD, NEW_PASSWORD)).status_code != 200
    assert (await Browser(env).sign_in(NEW, NEW_PASSWORD)).status_code != 200


def reset_links_to(smtp: SmtpFake, address: str) -> list[str]:
    return [m.token() for m in smtp.to(address) if "/reset#token=" in m.text]


# --- the changer's password change -----------------------------------------------------------


async def test_a_password_change_hashed_before_the_revert_does_not_land(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The change passed its session check and is hashing when the revert commits."""
    changer = await changed(menv, smtp)
    token = revert_token(smtp)
    hasher = menv.app.state.password_hasher
    original, inside, release = hasher.hash, asyncio.Event(), asyncio.Event()

    async def slow(password: Any) -> Any:
        hashed = await original(password)
        inside.set()
        await release.wait()
        return hashed

    monkeypatch.setattr(hasher, "hash", slow)
    changing = asyncio.ensure_future(changer.post(CHANGE, {"new_password": NEW_PASSWORD}))
    await inside.wait()
    async with own_client(menv) as other:
        assert (await revert(token, other)).status_code == 200
    release.set()
    refused = await changing
    assert (refused.status_code, reason(refused)) == (401, "not_authenticated")
    await locked_out(menv)


async def test_a_password_change_holding_the_lock_is_undone_by_the_waiting_revert(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    changer = await changed(menv, smtp)
    token = revert_token(smtp)
    inside, release = parked(monkeypatch, reset_routes, "lock_user")
    changing = asyncio.ensure_future(changer.post(CHANGE, {"new_password": NEW_PASSWORD}))
    await inside.wait()
    async with own_client(menv) as other:
        reverting = asyncio.ensure_future(revert(token, other))
        await until_it_waits(menv, reverting)
        release.set()
        assert (await changing).status_code == 200
        assert (await reverting).status_code == 200
    await locked_out(menv)


# --- a reset requested for the changer's address -----------------------------------------------


async def test_a_reset_request_looked_up_before_the_revert_issues_nothing(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request found the account under NEW; the revert commits before it issues a token."""
    await changed(menv, smtp)
    token = revert_token(smtp)
    inside, release = parked(monkeypatch, reset_routes, "find_user")
    async with own_client(menv) as asker, own_client(menv) as other:
        asking = asyncio.ensure_future(Browser(asker).post(ASK, {"email": NEW}))
        await inside.wait()
        assert (await revert(token, other)).status_code == 200
        release.set()
        assert (await asking).status_code == 202  # the same answer as for any address
    assert reset_links_to(smtp, NEW) == []
    assert await menv.scalar("SELECT array_agg(purpose) FROM auth_tokens") == ["reset_password"]
    [owners] = reset_links_to(smtp, OLD)  # the revert's own, to the restored address
    assert owners
    await locked_out(menv)


async def test_a_reset_token_issued_under_the_lock_is_deleted_by_the_waiting_revert(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    await changed(menv, smtp)
    token = revert_token(smtp)
    inside, release = parked(monkeypatch, reset_routes, "lock_user")
    async with own_client(menv) as asker, own_client(menv) as other:
        asking = asyncio.ensure_future(Browser(asker).post(ASK, {"email": NEW}))
        await inside.wait()
        reverting = asyncio.ensure_future(revert(token, other))
        await until_it_waits(menv, reverting)
        release.set()
        assert (await asking).status_code == 202
        assert (await reverting).status_code == 200
    monkeypatch.undo()
    [stale] = reset_links_to(smtp, NEW)  # mailed to the changer, but deleted by the revert
    used = await Browser(menv).post(
        COMPLETE, {"token": stale, "password": NEW_PASSWORD, "keep_methods": []}
    )
    assert (used.status_code, reason(used)) == (400, "invalid_token")
    await locked_out(menv)


# --- a reset completion: one lock order, no deadlock -------------------------------------------


async def test_a_reset_completion_waiting_for_the_revert_finds_its_token_gone(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before #1265's lock order the completion held its token row and waited for the user lock
    while the revert held that lock and wanted the token row: a deadlock and a 500."""
    await changed(menv, smtp)
    token = revert_token(smtp)
    assert (await Browser(menv).post(ASK, {"email": NEW})).status_code == 202
    [reset] = reset_links_to(smtp, NEW)
    inside, release = parked(monkeypatch, email_revert, "lock_user")
    async with own_client(menv) as completer, own_client(menv) as other:
        reverting = asyncio.ensure_future(revert(token, other))
        await inside.wait()  # the revert holds the user row lock
        completing = asyncio.ensure_future(
            Browser(completer).post(
                COMPLETE, {"token": reset, "password": NEW_PASSWORD, "keep_methods": []}
            )
        )
        await until_it_waits(menv, completing)
        release.set()
        assert (await reverting).status_code == 200
        refused = await completing
    assert (refused.status_code, reason(refused)) == (400, "invalid_token")
    await locked_out(menv)


async def test_a_reset_completion_holding_the_lock_is_undone_by_the_waiting_revert(
    menv: Env, smtp: SmtpFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    await changed(menv, smtp)
    token = revert_token(smtp)
    assert (await Browser(menv).post(ASK, {"email": NEW})).status_code == 202
    [reset] = reset_links_to(smtp, NEW)
    inside, release = parked(monkeypatch, reset_routes, "lock_user")
    async with own_client(menv) as completer, own_client(menv) as other:
        completing = asyncio.ensure_future(
            Browser(completer).post(
                COMPLETE, {"token": reset, "password": NEW_PASSWORD, "keep_methods": []}
            )
        )
        await inside.wait()
        reverting = asyncio.ensure_future(revert(token, other))
        await until_it_waits(menv, reverting)
        release.set()
        assert (await completing).status_code == 200
        assert (await reverting).status_code == 200
    await locked_out(menv)
