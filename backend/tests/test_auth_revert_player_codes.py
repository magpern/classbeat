"""The paths that burn the player sign-in codes in flight (#1292, #1294, ADR 0003).

An ``oidc_codes`` row lives 60 seconds and is single use. The changer who asked for one just
before the revert (or before the owner's reset, password change or sign out everywhere) must not
turn it into a player session after it: each of these paths burns the user's unused codes in its
own transaction, and a later redemption is refused as ``used`` (the same ``invalid_grant`` as any
bad code) and recorded for that user. Another user's codes are left alone. A code inserted while
such a path runs is ``test_oidc_advance_lock.py``.
"""

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from authtools import Env
from flows import Browser, user_with_password
from oidctools import FakePlayer, challenge_of, oidc_env
from smtpfake import SmtpFake
from test_auth_email_revert import NEW_PASSWORD, OLD, changed, revert, revert_token

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #1012

OTHER = "other@example.com"
INSERT = (
    "INSERT INTO oidc_codes (code_hash, user_id, client_id, redirect_uri, code_challenge, nonce, "
    "auth_time, expires_at) VALUES (:h, :u, 'classbeat-player', 'https://player.example/oidc/"
    "callback', :c, :n, now(), now() + interval '60 seconds')"
)


@pytest.fixture
async def penv(
    clean_auth: None, app_database_url: str, kek_dir: Path, smtp: SmtpFake
) -> AsyncIterator[Env]:
    async with oidc_env(
        app_database_url, kek_dir, smtp_host="127.0.0.1", smtp_port=smtp.port, smtp_tls="none"
    ) as running:
        yield running


async def plant(env: Env, user_id: UUID, code: str) -> FakePlayer:
    """An unused code of the user, as ``/oidc/authorize/continue`` leaves it."""
    player = FakePlayer(env)
    await env.run(
        INSERT,
        h=hashlib.sha256(code.encode()).digest(),
        u=user_id,
        c=challenge_of(player.verifier),
        n=player.nonce,
    )
    return player


async def refused_as_used(env: Env, user_id: UUID) -> list[object]:
    async with env.db() as db:
        rows = await db.execute(
            text(
                "SELECT detail FROM auth_events "
                "WHERE kind = 'oidc_code_refused' AND user_id = :u ORDER BY at, id"
            ),
            {"u": user_id},
        )
        return [row.detail for row in rows]


async def test_a_revert_burns_the_changers_unused_codes_and_only_theirs(
    penv: Env, smtp: SmtpFake
) -> None:
    await changed(penv, smtp)
    owner = await penv.scalar("SELECT id FROM users")
    bystander = await penv.user(OTHER)
    asked = await plant(penv, owner, "code-asked-by-the-changer")  # type: ignore[arg-type]
    other = await plant(penv, bystander, "code-of-someone-else")

    assert (await revert(revert_token(smtp), penv)).status_code == 200

    refused = await asked.token("code-asked-by-the-changer")
    assert (refused.status_code, refused.json()) == (400, {"error": "invalid_grant"})
    assert await refused_as_used(penv, owner) == [{"reason": "used"}]  # type: ignore[arg-type]
    assert (await other.token("code-of-someone-else")).status_code == 200


async def test_a_password_reset_burns_the_users_unused_codes_and_only_theirs(
    penv: Env, smtp: SmtpFake
) -> None:
    owner = await user_with_password(penv, OLD)
    bystander = await penv.user(OTHER)
    asked = await plant(penv, owner, "code-asked-before-the-reset")
    other = await plant(penv, bystander, "code-of-someone-else")
    asking = await Browser(penv).post("/api/auth/password/reset/request", {"email": OLD})
    assert asking.status_code == 202
    token = smtp.to(OLD)[-1].token()

    done = await Browser(penv).post(
        "/api/auth/password/reset/complete",
        {"token": token, "password": NEW_PASSWORD, "keep_methods": []},
    )
    assert done.status_code == 200

    refused = await asked.token("code-asked-before-the-reset")
    assert (refused.status_code, refused.json()) == (400, {"error": "invalid_grant"})
    assert await refused_as_used(penv, owner) == [{"reason": "used"}]
    assert (await other.token("code-of-someone-else")).status_code == 200


async def test_a_refused_revert_burns_nothing(penv: Env, smtp: SmtpFake) -> None:
    """A refusal rolls the revert's transaction back, the burn with it. This guards that rollback
    only because every refusal of the revert comes before ``_lock_down`` (here the stale address,
    found under the lock); it says nothing about a failure after the burn."""
    await changed(penv, smtp)
    owner = await penv.scalar("SELECT id FROM users")
    asked = await plant(penv, owner, "code-of-the-account")  # type: ignore[arg-type]
    await penv.run(
        "UPDATE users SET email = 'moved@example.org', email_normalized = 'moved@example.org'"
    )

    assert (await revert(revert_token(smtp), penv)).status_code == 400

    assert (await asked.token("code-of-the-account")).status_code == 200


@pytest.mark.parametrize(
    ("path", "body"),
    [
        pytest.param("/api/auth/password/change", {"new_password": NEW_PASSWORD}, id="password"),
        pytest.param("/api/auth/logout-all", None, id="sign-out-everywhere"),
    ],
)
async def test_a_password_change_and_sign_out_everywhere_burn_the_users_codes_and_only_theirs(
    penv: Env, path: str, body: dict[str, object] | None
) -> None:
    """Often the answer to a compromise (#1294): a code the intruder's session asked for in the
    last minute must not become a player session after the owner ended that session."""
    owner = await user_with_password(penv, OLD)
    bystander = await penv.user(OTHER)
    browser = Browser(penv)
    assert (await browser.sign_in(OLD)).status_code == 200  # recently authenticated
    asked = await plant(penv, owner, "code-of-the-intruder")
    other = await plant(penv, bystander, "code-of-someone-else")

    answer = await browser.post(path, body)
    assert answer.status_code in (200, 204), answer.text

    refused = await asked.token("code-of-the-intruder")
    assert (refused.status_code, refused.json()) == (400, {"error": "invalid_grant"})
    assert await refused_as_used(penv, owner) == [{"reason": "used"}]
    assert (await other.token("code-of-someone-else")).status_code == 200
