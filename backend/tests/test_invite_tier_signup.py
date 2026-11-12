"""The tier an invite carries, granted where the invite is consumed (ADR 0033 section 4).

A tier invite is seeded here as the superuser (an invite and its single-use code with
``invite_id``), so these tests reach the consume paths without the admin area: the provider
sign-up grants it like the password one; a code that is gone refuses the provider sign-up whole;
two confirmations racing for one invite give one account and one entitlement; and the grant
leaves ``app.user_id`` cleared for the rest of the sign-up transaction. #989: the provider
hand-off (an unverified or missing email, ``oauth_handoff``) grants the tier too; the setting is
cleared even when the redeem steps raise; and a refusal they raise (``AlreadyRedeemed``,
``TermsRefused``) rolls the sign-up back as ``invite_invalid``, never a 500, on every path (the
hand-off too, #994).
"""

import asyncio
import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from authtools import Env
from classbeat.auth import invite_tier
from classbeat.auth.invite_tier import grant_invite_tier
from classbeat.db import system_session
from classbeat.plans.entitle import TermsRefused
from classbeat.plans.redeem_steps import AlreadyRedeemed
from flows import PASSWORD, Browser, reason
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


# --- #989: the hand-off, and the redeem steps raising ---------------------------------------


async def hand_off_and_confirm(env: Env, fake: FakeProviders, smtp: SmtpFake, code: str) -> Any:
    """A Microsoft sign-up with no email (the hand-off), the address typed, the link followed."""
    browser = Browser(env)
    response = await sign_up_with(browser, fake, "microsoft", invite=code)
    assert location(response) == "/signin?oauth=email_needed"
    sent = await browser.post("/api/auth/oauth/pending/email", {"email": "new@example.com"})
    assert sent.status_code == 202
    await asyncio.sleep(0.3)
    token = smtp.to("new@example.com")[-1].token()
    return await browser.post("/api/auth/signup/confirm", {"token": token})


async def test_the_provider_hand_off_grants_the_invites_tier(
    oenv: Env, fake: FakeProviders, smtp: SmtpFake, superuser: Connection
) -> None:
    code, _ = tier_invite(superuser, plan="instructor", days=12)
    assert (await hand_off_and_confirm(oenv, fake, smtp, code)).status_code == 200
    assert entitlements(superuser) == [("instructor", "code", timedelta(days=12))]
    owner = superuser.execute(text("SELECT owner_id FROM code_redemptions")).scalar_one()
    assert owner == superuser.execute(text("SELECT id FROM users")).scalar_one()
    assert superuser.execute(text("SELECT provider FROM identities")).scalar_one() == "microsoft"


async def test_a_hand_off_whose_tier_code_is_gone_creates_nothing(
    oenv: Env, fake: FakeProviders, smtp: SmtpFake, superuser: Connection
) -> None:
    code, code_id = tier_invite(superuser)
    superuser.execute(
        text("UPDATE redeem_codes SET disabled_at = now() WHERE id = :i"), {"i": code_id}
    )
    response = await hand_off_and_confirm(oenv, fake, smtp, code)
    assert (response.status_code, reason(response)) == (409, "invite_invalid")
    assert superuser.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM pending_signups")).scalar_one() == 1
    assert superuser.execute(text("SELECT used_at FROM invites")).scalar_one() is None
    assert entitlements(superuser) == []


async def already_redeemed(db: AsyncSession, *_: object) -> Any:
    """As ``take`` raises it: after the unique index refused a statement, so the transaction (or
    its savepoint) is aborted when the exception leaves."""
    try:
        await db.execute(text("SELECT 1 / 0"))
    except DBAPIError:
        raise AlreadyRedeemed from None
    raise AssertionError("unreachable")


async def terms_refused(*_: object) -> Any:
    raise TermsRefused("an entitlement grants a plan or a feature")


REFUSALS = pytest.mark.parametrize("refusal", [already_redeemed, terms_refused])


@REFUSALS
async def test_a_refusal_of_the_redeem_steps_refuses_a_provider_sign_up_whole(
    oenv: Env,
    fake: FakeProviders,
    superuser: Connection,
    monkeypatch: pytest.MonkeyPatch,
    refusal: Any,
) -> None:
    code, _ = tier_invite(superuser)
    monkeypatch.setattr(invite_tier, "take", refusal)
    browser = Browser(oenv)
    response = await sign_up_with(browser, fake, "google", invite=code)
    assert location(response) == "/signup?oauth=invite_invalid"
    assert not browser.signed_in
    assert superuser.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    assert superuser.execute(text("SELECT used_at FROM invites")).scalar_one() is None
    assert superuser.execute(text("SELECT redeemed_count FROM redeem_codes")).scalar_one() == 0


@REFUSALS
async def test_a_refusal_of_the_redeem_steps_refuses_a_password_sign_up_whole(
    oenv: Env,
    superuser: Connection,
    smtp: SmtpFake,
    monkeypatch: pytest.MonkeyPatch,
    refusal: Any,
) -> None:
    code, _ = tier_invite(superuser)
    signed = await Browser(oenv).post(
        "/api/auth/signup", {"email": "new@example.com", "password": PASSWORD, "invite_code": code}
    )
    assert signed.status_code == 202
    await asyncio.sleep(0.3)
    token = smtp.to("new@example.com")[-1].token()
    monkeypatch.setattr(invite_tier, "take", refusal)
    response = await Browser(oenv).post(
        "/api/auth/signup/confirm", {"token": token, "password": PASSWORD}
    )
    assert (response.status_code, reason(response)) == (409, "invite_invalid")
    assert superuser.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM pending_signups")).scalar_one() == 1
    assert superuser.execute(text("SELECT used_at FROM invites")).scalar_one() is None


@REFUSALS
async def test_a_refusal_of_the_redeem_steps_refuses_a_hand_off_sign_up_whole(
    oenv: Env,
    fake: FakeProviders,
    smtp: SmtpFake,
    superuser: Connection,
    monkeypatch: pytest.MonkeyPatch,
    refusal: Any,
) -> None:
    """The hand-off's confirmation (#994): ``invite_invalid``, and nothing of the sign-up stays."""
    code, _ = tier_invite(superuser)
    monkeypatch.setattr(invite_tier, "take", refusal)
    response = await hand_off_and_confirm(oenv, fake, smtp, code)
    assert (response.status_code, reason(response)) == (409, "invite_invalid")
    for table in ("users", "identities", "code_redemptions"):
        assert superuser.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table  # noqa: S608
    assert superuser.execute(text("SELECT count(*) FROM pending_signups")).scalar_one() == 1
    assert superuser.execute(text("SELECT used_at FROM invites")).scalar_one() is None
    assert superuser.execute(text("SELECT redeemed_count FROM redeem_codes")).scalar_one() == 0
    assert entitlements(superuser) == []


async def test_the_user_setting_is_cleared_when_the_redeem_steps_raise(
    env: Env, superuser: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    tier_invite(superuser)
    invite_id = superuser.execute(text("SELECT invite_id FROM redeem_codes")).scalar_one()
    user_id = await env.user("new@example.com")
    seen: list[str] = []

    async def broken(db: AsyncSession, *_: object) -> Any:
        seen.append((await db.execute(text("SELECT current_setting('app.user_id')"))).scalar_one())
        raise RuntimeError("an unexpected failure")

    monkeypatch.setattr(invite_tier, "take", broken)
    async with system_session(env.factory) as db:
        with pytest.raises(RuntimeError):
            await grant_invite_tier(db, invite_id, user_id)
        assert seen == [str(user_id)]  # it was set for the steps
        setting = await db.execute(text("SELECT current_setting('app.user_id', true)"))
        assert setting.scalar_one() == ""  # and is cleared, the transaction still usable
    assert entitlements(superuser) == []
