"""The lock order of ADR 0019 section 6 for the register start and finish, the remove (#772), the
sign-in finish (#773) and the re-auth finish (#774).

Deterministic, not timed (as ``test_auth_reset_races``): the test holds the ``users`` row lock,
lets the request run until Postgres reports it waiting for a lock, then either releases the lock
unchanged or plays a reset (revoke the user's sessions) before releasing it. Both sides take the
user row first, so they never deadlock: the finish either completes or finds its session gone and
stores nothing.
"""

import contextlib
from typing import Any

import pytest
from sqlalchemy import text

from authtools import Env
from classbeat.auth import passkey_signin_session
from passkeysignin import (
    SESSIONS,
    Holder,
    anonymous,
    finish_signin,
    start_signin,
    with_passkey,
)
from passkeytools import (
    Client,
    count,
    finish,
    mails,
    plant,
    reason,
    signed_in,
    start,
    user_id_of,
)
from passkeytools import penv as penv
from smtpfake import SmtpFake
from test_auth_reset_races import against_a_held_lock
from test_passkey_reauth import REAUTH_START, ready
from test_passkey_reauth import finish as reauth_finish
from webauthnfake import SoftAuthenticator

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

CREDENTIALS = "SELECT count(*) FROM webauthn_credentials"
CHALLENGES = "SELECT count(*) FROM webauthn_challenges"
REVOKE = "DELETE FROM sessions WHERE user_id = :u"  # what a reset does under the same lock


async def test_a_register_finish_waits_for_the_user_row_lock_then_completes(penv: Env) -> None:
    client = await signed_in(penv)
    credential = SoftAuthenticator().create((await start(client)).json())
    response = await against_a_held_lock(penv, await user_id_of(penv), finish(client, credential))
    assert response.status_code == 201, response.text
    assert await count(penv, CREDENTIALS) == 1


async def test_a_register_finish_racing_a_reset_stores_nothing(penv: Env) -> None:
    client = await signed_in(penv)
    credential = SoftAuthenticator().create((await start(client)).json())
    user = await user_id_of(penv)
    response = await against_a_held_lock(penv, user, finish(client, credential), REVOKE)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, CREDENTIALS) == 0
    assert await count(penv, "SELECT count(*) FROM auth_events WHERE kind = 'passkey_added'") == 0


async def test_a_remove_waits_for_the_user_row_lock_then_completes(penv: Env) -> None:
    client = await signed_in(penv)
    user = await user_id_of(penv)
    passkey = await plant(penv, user)
    removal = client.request("DELETE", f"/api/auth/passkeys/{passkey}")
    response = await against_a_held_lock(penv, user, removal)
    assert response.status_code == 204
    assert await count(penv, CREDENTIALS) == 0


# --- the register start (#782 review N3) -------------------------------------------------------


async def test_a_register_start_replacing_a_flow_waits_for_the_user_row_lock(penv: Env) -> None:
    """With a previous register cookie the start deletes that flow's row; it takes the user row
    first, so it waits here instead of deadlocking with a reset (whose session delete cascades
    to the same challenge row)."""
    client = await signed_in(penv)
    assert (await start(client)).status_code == 200
    response = await against_a_held_lock(penv, await user_id_of(penv), start(client))
    assert response.status_code == 200, response.text
    assert await count(penv, CHALLENGES) == 1


async def test_a_register_start_racing_a_reset_stores_no_flow(penv: Env) -> None:
    client = await signed_in(penv)
    assert (await start(client)).status_code == 200
    user = await user_id_of(penv)
    response = await against_a_held_lock(penv, user, start(client), REVOKE)
    assert (response.status_code, reason(response)) == (401, "not_authenticated")
    assert await count(penv, CHALLENGES) == 0


# --- the sign-in finish (#773) -----------------------------------------------------------------


async def signin_ready(env: Env) -> tuple[Holder, Client, dict[str, Any]]:
    """A user with a passkey, and an anonymous browser holding a started flow and its assertion."""
    holder = await with_passkey(env)
    client = anonymous(env)
    begun = await start_signin(client)
    return holder, client, holder.device.get(begun.json(), holder.handle)


async def test_a_signin_finish_waits_for_the_user_row_lock_then_completes(penv: Env) -> None:
    holder, client, assertion = await signin_ready(penv)
    response = await against_a_held_lock(penv, holder.user_id, finish_signin(client, assertion))
    assert response.status_code == 200, response.text
    assert await count(penv, SESSIONS) == 1


async def test_a_signin_finish_racing_a_reset_that_drops_its_passkey_makes_no_session(
    penv: Env, smtp: SmtpFake
) -> None:
    holder, client, assertion = await signin_ready(penv)
    mailed = len(smtp.to(holder.email))  # the "passkey added" notice of the registration
    reset = ("DELETE FROM webauthn_credentials WHERE user_id = :u", REVOKE)
    response = await against_a_held_lock(
        penv, holder.user_id, finish_signin(client, assertion), *reset
    )
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, SESSIONS) == 0
    # Section 5.5.2: a credential gone under the lock writes nothing. Not a sign-in, and not
    # mistaken for a counter regression (no event, no failure and no mail; #783 review N2).
    assert (
        await count(
            penv,
            "SELECT count(*) FROM auth_events WHERE (kind = 'sign_in' "
            "AND detail->>'method' = 'passkey') "
            "OR kind IN ('sign_in_failed', 'passkey_counter_regressed')",
        )
        == 0
    )
    assert len(await mails(smtp, holder.email, mailed + 1)) == mailed


async def test_a_reset_after_a_signin_finish_revokes_its_session(penv: Env) -> None:
    holder, client, assertion = await signin_ready(penv)
    assert (await finish_signin(client, assertion)).status_code == 200
    async with penv.db() as db:  # the reset: the user row first, then what it deletes
        await db.execute(
            text("SELECT id FROM users WHERE id = :u FOR NO KEY UPDATE"), {"u": holder.user_id}
        )
        await db.execute(
            text("DELETE FROM webauthn_credentials WHERE user_id = :u"), {"u": holder.user_id}
        )
        await db.execute(text(REVOKE), {"u": holder.user_id})
    assert await count(penv, SESSIONS) == 0
    assert (await client.get("/api/auth/session")).json()["authenticated"] is False


async def test_the_counter_grant_and_session_are_one_transaction(
    penv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure in ``start_session`` rolls back the counter update made before it."""
    holder, client, _ = await signin_ready(penv)
    holder.device.sign_count = 3
    begun = await start_signin(client)
    assertion = holder.device.get(begun.json(), holder.handle)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("start_session failed")

    monkeypatch.setattr(passkey_signin_session, "start_session", broken)
    with contextlib.suppress(RuntimeError):  # the test transport re-raises server errors
        await finish_signin(client, assertion)
    assert await count(penv, "SELECT sign_count FROM webauthn_credentials") == 0
    assert await penv.scalar("SELECT last_used_at FROM webauthn_credentials") is None
    assert await count(penv, SESSIONS) == 0


# --- the re-auth finish (#774) -----------------------------------------------------------------

REAUTHED = (
    "SELECT count(*) FROM auth_events WHERE kind = 'reauth' AND detail->>'method' = 'passkey'"
)


async def reauth_ready(env: Env, sign_count: int = 0) -> tuple[Client, Any, dict[str, Any]]:
    """A signed-in user with a passkey, a started re-auth flow and its assertion."""
    client, device = await ready(env)
    device.sign_count = sign_count
    begun = await client.post(REAUTH_START)
    return client, await user_id_of(env), device.get(begun.json(), None)


async def test_a_reauth_finish_waits_for_the_user_row_lock_then_completes(penv: Env) -> None:
    client, user, assertion = await reauth_ready(penv)
    response = await against_a_held_lock(penv, user, reauth_finish(client, assertion))
    assert response.status_code == 200, response.text
    assert await count(penv, REAUTHED) == 1


async def test_a_reauth_finish_racing_a_reset_that_drops_its_passkey_rotates_nothing(
    penv: Env,
) -> None:
    client, user, assertion = await reauth_ready(penv)
    reset = ("DELETE FROM webauthn_credentials WHERE user_id = :u", REVOKE)
    response = await against_a_held_lock(penv, user, reauth_finish(client, assertion), *reset)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, SESSIONS) == 0  # no rotated session outlives the reset
    assert await count(penv, REAUTHED) == 0
    written = (
        "SELECT count(*) FROM auth_events WHERE kind IN ('passkey_counter_regressed', "
        "'reauth_failed')"
    )
    assert await count(penv, written) == 0  # gone: nothing written


async def test_a_reauth_finish_racing_a_disable_refuses_and_leaves_the_counter(
    penv: Env,
) -> None:
    """The disabled check runs inside the write transaction, under the lock (#784 review N2)."""
    client, user, assertion = await reauth_ready(penv, sign_count=3)
    disable = "UPDATE users SET disabled_at = now() WHERE id = :u"
    response = await against_a_held_lock(penv, user, reauth_finish(client, assertion), disable)
    assert (response.status_code, reason(response)) == (400, "passkey_failed")
    assert await count(penv, REAUTHED) == 0
    disabled = (
        "SELECT count(*) FROM auth_events WHERE kind = 'reauth_failed' "
        "AND detail->>'reason' = 'disabled'"
    )
    assert await count(penv, disabled) == 1
    assert await count(penv, "SELECT sign_count FROM webauthn_credentials") == 0
    assert await penv.scalar("SELECT last_used_at FROM webauthn_credentials") is None


async def test_a_reauth_finish_racing_a_reset_that_keeps_the_passkey_rolls_the_counter_back(
    penv: Env,
) -> None:
    client, user, assertion = await reauth_ready(penv, sign_count=3)
    response = await against_a_held_lock(penv, user, reauth_finish(client, assertion), REVOKE)
    assert (response.status_code, reason(response)) == (401, "not_authenticated")
    assert await count(penv, SESSIONS) == 0
    assert await count(penv, REAUTHED) == 0
    assert await count(penv, "SELECT sign_count FROM webauthn_credentials") == 0
    assert await penv.scalar("SELECT last_used_at FROM webauthn_credentials") is None
