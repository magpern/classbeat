"""ME3 notices carry the recipient's account id at every route call site (ADR 0022 section 4).

With the id the send path reads the mail preferences by id (``live.send_optional``); without it,
by address, which finds no account once the address changed and so sends nothing. Each test runs
the real route and records the notice it queued, then checks it names the account that did the
thing. Dropping ``user_id`` at any of the six call sites fails one of them:
``auth/identity_routes.py`` (unlink), ``auth/mfa_routes.py`` (TOTP off), ``auth/oauth_outcomes.py``
(link and provider sign-up) and ``auth/reset_routes.py`` (reset and password change).
"""

from typing import Any
from uuid import UUID

import httpx
import pytest

from authtools import ORIGIN, Env
from classbeat.emails import notices
from flows import Browser, only_user_id, signed_in, user_with_password
from mfatools import Actor
from oauthtools import location, make_invite, sign_up_with
from oidcfake import FakeProviders
from smtpfake import SmtpFake
from test_account_notices import NEW
from test_auth_oauth_link import add_identity, link
from test_auth_reset import ask, complete


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[notices.Notice]:
    """Every notice a route queued, still delivered by the real task."""
    recorded: list[notices.Notice] = []
    real = notices.send_notice

    async def record(request_app: Any, notice: notices.Notice) -> None:
        recorded.append(notice)
        await real(request_app, notice)

    monkeypatch.setattr(notices, "send_notice", record)
    return recorded


def only(seen: list[notices.Notice], kind_id: str) -> notices.Notice:
    [notice] = [n for n in seen if n.kind_id == kind_id]
    return notice


async def test_unlinking_names_the_account(
    menv: Env, smtp: SmtpFake, seen: list[notices.Notice]
) -> None:
    browser = await signed_in(menv, "me@example.com")
    user = await only_user_id(menv)
    identity = await add_identity(menv, user, "github")
    assert (await browser.request("DELETE", f"/api/auth/identities/{identity}")).status_code == 204
    assert only(seen, "identity_unlinked").user_id == user


async def test_turning_totp_off_names_the_account(
    menv: Env, smtp: SmtpFake, seen: list[notices.Notice]
) -> None:
    user = await menv.user("me@example.com")
    await menv.run(
        "INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, confirmed_at) "
        "VALUES (:u, 'x', 'y', 'app-v1', now())",
        u=user,
    )
    issued = await menv.login(user, mfa=True)
    await menv.run("UPDATE sessions SET stepup_at = now()")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=menv.app), base_url=ORIGIN
    ) as client:
        response = await Actor(menv, issued.token, client).call("DELETE", "/api/auth/mfa/totp")
    assert response.status_code == 204, response.text
    assert only(seen, "totp_removed").user_id == user


async def test_a_completed_reset_names_the_account(
    menv: Env, smtp: SmtpFake, seen: list[notices.Notice]
) -> None:
    user = await user_with_password(menv)
    await ask(Browser(menv))
    token = smtp.messages[-1].token()
    assert (await complete(Browser(menv), token)).status_code == 200
    assert only(seen, "password_changed").user_id == user


async def test_a_password_change_names_the_account(
    menv: Env, smtp: SmtpFake, seen: list[notices.Notice]
) -> None:
    browser = await signed_in(menv)
    user = await only_user_id(menv)
    response = await browser.post("/api/auth/password/change", {"new_password": NEW})
    assert response.status_code == 200
    assert only(seen, "password_changed").user_id == user


async def test_linking_a_provider_names_the_account(
    oenv: Env, fake: FakeProviders, smtp: SmtpFake, seen: list[notices.Notice]
) -> None:
    browser = await signed_in(oenv, "me@example.com")
    user = await only_user_id(oenv)
    done = await link(browser, fake, sub="gsub", email="other@example.com")
    assert location(done) == "/account?oauth=linked"
    assert only(seen, "identity_linked").user_id == user


async def test_a_provider_sign_up_names_the_new_account(
    oenv: Env, fake: FakeProviders, smtp: SmtpFake, seen: list[notices.Notice]
) -> None:
    browser = Browser(oenv)
    response = await sign_up_with(browser, fake, "google", invite=await make_invite(oenv))
    assert location(response) == "/"
    user = await only_user_id(oenv)
    assert isinstance(user, UUID)
    assert only(seen, "welcome").user_id == user
