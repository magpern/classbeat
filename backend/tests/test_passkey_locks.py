"""The lock order of ADR 0019 section 6 for the register finish and the remove (#772).

Deterministic, not timed (as ``test_auth_reset_races``): the test holds the ``users`` row lock,
lets the request run until Postgres reports it waiting for a lock, then either releases the lock
unchanged or plays a reset (revoke the user's sessions) before releasing it. Both sides take the
user row first, so they never deadlock: the finish either completes or finds its session gone and
stores nothing.
"""

import pytest

from authtools import Env
from passkeytools import (
    count,
    finish,
    plant,
    reason,
    signed_in,
    start,
    user_id_of,
)
from passkeytools import penv as penv
from test_auth_reset_races import against_a_held_lock
from webauthnfake import SoftAuthenticator

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")

CREDENTIALS = "SELECT count(*) FROM webauthn_credentials"
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
