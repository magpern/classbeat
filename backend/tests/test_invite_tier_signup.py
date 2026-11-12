"""The tier an invite carries, granted where the invite is consumed (ADR 0033 section 4).

A tier invite is seeded here as the superuser (an invite and its single-use code with
``invite_id``), so these tests reach the consume paths without the admin area: the provider
sign-up grants it like the password one; a code that is gone refuses the provider sign-up whole;
two confirmations racing for one invite give one account and one entitlement; and the grant
leaves ``app.user_id`` cleared for the rest of the sign-up transaction.
"""

import asyncio
import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from authtools import Env
from classbeat.auth.invite_tier import grant_invite_tier
from classbeat.db import system_session
from flows import PASSWORD, Browser
from oauthtools import location, sign_up_with
from oidcfake import FakeProviders
from smtpfake import SmtpFake


def tier_invite(conn: Connection, plan: str = "pro", days: int = 20) -> tuple[str, uuid.UUID]:
    """An unused invite with a tier, as an approval leaves it: its code and the tier's code id."""
    code = secrets.token_urlsafe(16)
    invite_id = conn.execute(
        text(
            "INSERT INTO invites (code_hash, expires_at) VALUES (:h, now() + interval '1 day') "
            "RETURNING id"
        ),
        {"h": hashlib.sha256(code.encode()).digest()},
    ).scalar_one()
    code_id = conn.execute(
        text(
            "INSERT INTO redeem_codes (code_hash, label, plan_id, grant_days, max_redemptions, "
            "expires_at, invite_id) VALUES (:h, 'Invite request', :p, :d, 1, "
            "now() + interval '1 day', :i) RETURNING id"
        ),
        {"h": secrets.token_bytes(32), "p": plan, "d": days, "i": invite_id},
    ).scalar_one()
    return code, code_id


def entitlements(conn: Connection) -> list[tuple[str, str, timedelta]]:
    return [
        (r.plan_id, r.source, r.span)
        for r in conn.execute(
            text("SELECT plan_id, source, ends_at - starts_at AS span FROM entitlements")
        )
    ]


async def test_a_provider_sign_up_grants_the_invites_tier(
    oenv: Env, fake: FakeProviders, superuser: Connection
) -> None:
    code, _ = tier_invite(superuser)
    browser = Browser(oenv)
    assert location(await sign_up_with(browser, fake, "google", invite=code)) == "/"
    assert entitlements(superuser) == [("pro", "code", timedelta(days=20))]
    owner = superuser.execute(text("SELECT owner_id FROM code_redemptions")).scalar_one()
    assert owner == superuser.execute(text("SELECT id FROM users")).scalar_one()


async def test_a_provider_sign_up_whose_tier_code_is_gone_creates_nothing(
    oenv: Env, fake: FakeProviders, superuser: Connection
) -> None:
    code, code_id = tier_invite(superuser)
    superuser.execute(
        text("UPDATE redeem_codes SET disabled_at = now() WHERE id = :i"), {"i": code_id}
    )
    browser = Browser(oenv)
    response = await sign_up_with(browser, fake, "google", invite=code)
    assert location(response) == "/signup?oauth=invite_invalid"
    assert not browser.signed_in
    assert superuser.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM identities")).scalar_one() == 0
    assert superuser.execute(text("SELECT used_at FROM invites")).scalar_one() is None
    assert entitlements(superuser) == []


async def test_two_confirmations_racing_for_one_invite_grant_the_tier_once(
    oenv: Env, superuser: Connection, smtp: SmtpFake
) -> None:
    code, _ = tier_invite(superuser)
    addresses = ("one@example.com", "two@example.com")
    for address in addresses:
        signed = await Browser(oenv).post(
            "/api/auth/signup", {"email": address, "password": PASSWORD, "invite_code": code}
        )
        assert signed.status_code == 202
    await asyncio.sleep(0.3)
    tokens = [smtp.to(address)[-1].token() for address in addresses]
    answers = await asyncio.gather(
        *(
            Browser(oenv).post("/api/auth/signup/confirm", {"token": t, "password": PASSWORD})
            for t in tokens
        )
    )
    assert sorted(a.status_code for a in answers) == [200, 409]
    assert superuser.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
    assert entitlements(superuser) == [("pro", "code", timedelta(days=20))]
    assert superuser.execute(text("SELECT redeemed_count FROM redeem_codes")).scalar_one() == 1


async def test_the_grant_clears_the_user_setting_for_the_rest_of_the_transaction(
    env: Env, superuser: Connection
) -> None:
    _, code_id = tier_invite(superuser, plan="instructor", days=7)
    invite_id = superuser.execute(text("SELECT invite_id FROM redeem_codes")).scalar_one()
    user_id = await env.user("new@example.com")
    async with system_session(env.factory) as db:
        assert await grant_invite_tier(db, invite_id, user_id)
        setting = await db.execute(text("SELECT current_setting('app.user_id', true)"))
        assert setting.scalar_one() == ""
        # Owned rows are out of reach again, the new entitlement included.
        assert (await db.execute(text("SELECT count(*) FROM entitlements"))).scalar_one() == 0
    assert entitlements(superuser) == [("instructor", "code", timedelta(days=7))]
    # A plain invite (no code) and no invite grant nothing and need no setting.
    async with system_session(env.factory) as db:
        assert await grant_invite_tier(db, None, user_id)
        assert await grant_invite_tier(db, uuid.uuid4(), user_id)
    assert (
        superuser.execute(
            text("SELECT redeemed_count FROM redeem_codes WHERE id = :i"), {"i": code_id}
        ).scalar_one()
        == 1
    )
