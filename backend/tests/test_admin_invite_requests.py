"""The admin's invite requests (ADR 0033 sections 3 to 5), end to end against the database.

The gate on every route; the list; approve with a tier makes ONE code that passes the invite gate
and grants the tier exactly once through the redeem path when the account is created; the code is
single-use and expires; a plain approve grants nothing; decline; the refusals; the optional mail
(the SMTP fake; nothing when mail is off); no code or address in an audit row or a log line.
"""

import asyncio
import io
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems
from classbeat.log import JsonFormatter
from flows import PASSWORD, Browser
from smtpfake import SmtpFake

PATH = "/api/admin/invite-requests"
REQUESTER = "Rider@Example.com"
NORMALIZED = "rider@example.com"
TIER = {"plan_id": "instructor", "grant_days": 30}


def maint_settings(maint_database_url: str) -> dict[str, Any]:
    url = make_url(maint_database_url)
    return {
        "maint_database_url": url.set(password=None).render_as_string(hide_password=False),
        "database_maint_password": url.password,
    }


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str, smtp: SmtpFake) -> AsyncIterator[Env]:
    async with running_env(
        app_database_url,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
        **maint_settings(maint_database_url),
    ) as env:
        yield env


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


async def stepped_admin(env: Env, make_actor: Any, email: str = "a@example.com") -> Any:
    user_id = await env.user(email, role="admin")
    actor = await make_actor(user_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


def add_request(conn: Connection, email: str = REQUESTER, locale: str | None = "sv") -> uuid.UUID:
    return conn.execute(
        text(
            "INSERT INTO invite_requests (email, email_normalized, heard_from, instructor, locale) "
            "VALUES (:e, lower(:e), 'a friend', true, :l) RETURNING id"
        ),
        {"e": email, "l": locale},
    ).scalar_one()


def audit(conn: Connection) -> list[tuple[str, str, str | None, Any]]:
    return [
        (r.action, r.outcome, r.reason, r.params)
        for r in conn.execute(
            text("SELECT action, outcome, reason, params FROM admin_audit ORDER BY created_at, id")
        )
    ]


async def sign_up(env: Env, code: str, email: str = REQUESTER) -> Any:
    return await Browser(env).post(
        "/api/auth/signup", {"email": email, "password": PASSWORD, "invite_code": code}
    )


async def confirm(env: Env, smtp: SmtpFake, email: str = REQUESTER) -> Any:
    await asyncio.sleep(0.2)
    token = smtp.to(email)[-1].token()
    return await Browser(env).post(
        "/api/auth/signup/confirm", {"token": token, "password": PASSWORD}
    )


def routes_for(request_id: object) -> list[tuple[str, str, Any]]:
    return [
        ("GET", PATH, None),
        ("POST", f"{PATH}/{request_id}/approve", TIER),
        ("POST", f"{PATH}/{request_id}/decline", None),
    ]


async def send(actor: Any, method: str, path: str, body: Any = None) -> Any:
    return await (actor.get(path) if method == "GET" else actor.call(method, path, body))


# --- the gate ---------------------------------------------------------------------------------


async def test_the_router_guard_covers_the_routes(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {PATH, f"{PATH}/{{request_id}}/approve", f"{PATH}/{{request_id}}/decline"} <= paths


async def test_a_non_admin_gets_403_and_nothing_changes(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    request_id = add_request(superuser)
    actor = await make_actor(await env.user("plain@example.com"), mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now()")
    for method, path, body in routes_for(request_id):
        response = await send(actor, method, path, body)
        assert (response.status_code, reason(response)) == (403, "forbidden"), path
    assert audit(superuser) == []
    assert superuser.execute(text("SELECT state FROM invite_requests")).scalar_one() == "pending"
    assert superuser.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0


async def test_mfa_and_stepup_are_required_and_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    request_id = add_request(superuser)
    admin = await env.user("a@example.com", role="admin")
    no_mfa = await make_actor(admin)
    for method, path, body in routes_for(request_id):
        response = await send(no_mfa, method, path, body)
        assert (response.status_code, reason(response)) == (403, "mfa_required"), path
    no_stepup = await make_actor(admin, mfa=True)
    for method, path, body in routes_for(request_id)[1:]:
        response = await send(no_stepup, method, path, body)
        assert (response.status_code, reason(response)) == (403, "stepup_required"), path
        assert response.headers["cache-control"] == "no-store"
    assert [a[:3] for a in audit(superuser)] == [
        ("admin.request", "refused", "mfa_required")
    ] * 3 + [("admin.request", "refused", "stepup_required")] * 2
    assert superuser.execute(text("SELECT state FROM invite_requests")).scalar_one() == "pending"


# --- list -------------------------------------------------------------------------------------


async def test_the_list_shows_requests_newest_first_and_filters_by_state(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    first = add_request(superuser, "first@example.com", None)
    second = add_request(superuser, REQUESTER)
    await env.user(REQUESTER)  # the second address already has an account
    assert (await actor.post(f"{PATH}/{first}/decline")).status_code == 200
    response = await actor.get(PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["next_offset"] is None
    assert [(r["id"], r["state"], r["has_account"]) for r in page["requests"]] == [
        (str(second), "pending", True),
        (str(first), "declined", False),
    ]
    shown = page["requests"][0]
    assert (shown["email"], shown["heard_from"], shown["instructor"], shown["locale"]) == (
        REQUESTER,
        "a friend",
        True,
        "sv",
    )
    declined = page["requests"][1]
    assert declined["decided_by"] == {"id": str(actor.user_id), "email": "a@example.com"}
    pending = (await actor.get(f"{PATH}?state=pending&limit=1")).json()
    assert [r["id"] for r in pending["requests"]] == [str(second)]
    assert (await actor.get(f"{PATH}?state=other")).status_code == 422
    params = [a[3] for a in audit(superuser) if a[0] == "invite_request.list"]
    assert params[:2] == [
        {"state": None, "offset": 0, "limit": 50},
        {"state": "pending", "offset": 0, "limit": 1},
    ]


# --- approve with a tier ----------------------------------------------------------------------


async def test_an_issued_code_signs_up_once_and_grants_the_tier_once(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    request_id = add_request(superuser)
    response = await actor.post(f"{PATH}/{request_id}/approve", {**TIER, "expires_days": 5})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    issued = response.json()
    code, shown = issued["code"], issued["request"]
    assert issued["email"] == "not_requested"
    assert (shown["state"], shown["plan_id"], shown["grant_days"]) == ("issued", "instructor", 30)
    assert shown["invite"]["state"] == "active"
    tier = superuser.execute(
        text(
            "SELECT r.label, r.plan_id, r.features, r.grant_days, r.max_redemptions, "
            "r.redeemed_count, r.expires_at = i.expires_at AS same_expiry, "
            "i.expires_at - i.created_at AS span FROM redeem_codes r JOIN invites i "
            "ON i.id = r.invite_id"
        )
    ).one()
    assert tuple(tier) == ("Invite request", "instructor", [], 30, 1, 0, True, timedelta(days=5))
    # The one code passes the invite gate and, at the account's creation, grants the tier.
    assert (await sign_up(env, code)).status_code == 202
    assert superuser.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0
    confirmed = await confirm(env, smtp)
    assert (confirmed.status_code, confirmed.json()) == (200, {"status": "created"})
    user_id = superuser.execute(
        text("SELECT id FROM users WHERE email_normalized = :e"), {"e": NORMALIZED}
    ).scalar_one()
    [granted] = superuser.execute(
        text(
            "SELECT owner_id, plan_id, source, ends_at - starts_at AS span, code_id "
            "FROM entitlements"
        )
    ).all()
    assert (granted.owner_id, granted.plan_id, granted.source, granted.span) == (
        user_id,
        "instructor",
        "code",
        timedelta(days=30),
    )
    assert superuser.execute(text("SELECT count(*) FROM code_redemptions")).scalar_one() == 1
    assert superuser.execute(text("SELECT redeemed_count FROM redeem_codes")).scalar_one() == 1
    # The plan the user now has, through the app's own resolution.
    browser = Browser(env)
    assert (await browser.sign_in(REQUESTER)).status_code == 200
    plan = (await browser.get("/api/account/plan")).json()
    assert (plan["plan"], plan["source"]) == ("instructor", "code")
    # Single use: the code cannot sign anyone else up.
    again = await sign_up(env, code, "other@example.com")
    assert (again.status_code, again.json()["detail"]["reason"]) == (400, "invalid_invite")
    listed = (await actor.get(PATH)).json()["requests"][0]
    assert listed["invite"]["state"] == "used"
    # The audit row has the terms, never the code or the address.
    [(_, outcome, _, params)] = [a for a in audit(superuser) if a[0] == "invite_request.approve"]
    assert (outcome, params) == (
        "ok",
        {"plan_id": "instructor", "grant_days": 30, "expires_days": 5, "send_email": False},
    )
    every = str(audit(superuser)).lower()
    assert code.lower() not in every
    assert NORMALIZED not in every


async def test_a_plain_approve_is_an_invite_without_a_tier(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    request_id = add_request(superuser)
    issued = (await actor.post(f"{PATH}/{request_id}/approve", {})).json()
    assert (issued["request"]["plan_id"], issued["request"]["grant_days"]) == (None, None)
    assert superuser.execute(text("SELECT count(*) FROM redeem_codes")).scalar_one() == 0
    assert (await sign_up(env, issued["code"])).status_code == 202
    assert (await confirm(env, smtp)).status_code == 200
    assert superuser.execute(text("SELECT count(*) FROM users WHERE role = 'user'")).scalar_one()
    assert superuser.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0


async def test_an_expired_code_is_refused(env: Env, make_actor: Any, superuser: Connection) -> None:
    actor = await stepped_admin(env, make_actor)
    issued = (await actor.post(f"{PATH}/{add_request(superuser)}/approve", TIER)).json()
    superuser.execute(text("UPDATE invites SET expires_at = created_at"))
    refused = await sign_up(env, issued["code"])
    assert (refused.status_code, refused.json()["detail"]["reason"]) == (400, "invalid_invite")


async def test_a_tier_code_disabled_before_confirmation_refuses_the_sign_up_whole(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    issued = (await actor.post(f"{PATH}/{add_request(superuser)}/approve", TIER)).json()
    assert (await sign_up(env, issued["code"])).status_code == 202
    code_id = superuser.execute(text("SELECT id FROM redeem_codes")).scalar_one()
    assert (await actor.post(f"/api/admin/redeem-codes/{code_id}/disable")).status_code == 200
    late = await confirm(env, smtp)
    assert (late.status_code, late.json()["detail"]["reason"]) == (409, "invite_invalid")
    # Nothing is left: no account, no entitlement, the invite unused and the pending row kept.
    assert (
        superuser.execute(text("SELECT count(*) FROM users WHERE role = 'user'")).scalar_one() == 0
    )
    assert superuser.execute(text("SELECT count(*) FROM entitlements")).scalar_one() == 0
    assert superuser.execute(text("SELECT used_at FROM invites")).scalar_one() is None
    assert superuser.execute(text("SELECT redeemed_count FROM redeem_codes")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM pending_signups")).scalar_one() == 1


# --- refusals and decline ---------------------------------------------------------------------


async def test_decline_and_a_second_decision_are_refused(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    request_id = add_request(superuser)
    declined = await actor.post(f"{PATH}/{request_id}/decline")
    assert declined.status_code == 200, declined.text
    assert (declined.json()["state"], declined.json()["invite"]) == ("declined", None)
    for path, body in (
        (f"{PATH}/{request_id}/approve", TIER),
        (f"{PATH}/{request_id}/decline", None),
    ):
        again = await actor.post(path, body)
        assert (again.status_code, reason(again)) == (409, "already_decided")
    ghost = uuid.uuid4()
    for path in (f"{PATH}/{ghost}/approve", f"{PATH}/{ghost}/decline"):
        response = await actor.post(path, {})
        assert (response.status_code, reason(response)) == (404, "not_found")
    assert superuser.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0
    assert [a[:3] for a in audit(superuser)] == [
        ("invite_request.decline", "ok", None),
        ("invite_request.approve", "refused", "already_decided"),
        ("invite_request.decline", "refused", "already_decided"),
        ("invite_request.approve", "refused", "not_found"),
        ("invite_request.decline", "refused", "not_found"),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"plan_id": "instructor"},
        {"grant_days": 30},
        {**TIER, "grant_days": 0},
        {**TIER, "grant_days": 367},
        {**TIER, "expires_days": 31},
        {**TIER, "send_email": "yes"},
        {**TIER, "code": "mine"},
    ],
)
async def test_bad_terms_are_422_and_issue_nothing(
    env: Env, make_actor: Any, superuser: Connection, body: dict[str, Any]
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.post(f"{PATH}/{add_request(superuser)}/approve", body)
    assert response.status_code == 422
    assert superuser.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0


async def test_an_unknown_plan_is_refused_and_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    request_id = add_request(superuser)
    response = await actor.post(f"{PATH}/{request_id}/approve", {**TIER, "plan_id": "gold"})
    assert (response.status_code, reason(response)) == (422, "unknown_plan")
    assert superuser.execute(text("SELECT state FROM invite_requests")).scalar_one() == "pending"
    assert superuser.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0
    assert [a[:3] for a in audit(superuser)] == [
        ("invite_request.approve", "refused", "unknown_plan")
    ]


# --- mail -------------------------------------------------------------------------------------


async def test_the_code_is_mailed_in_the_requesters_language_when_asked(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    request_id = add_request(superuser, locale="sv")
    issued = (await actor.post(f"{PATH}/{request_id}/approve", {**TIER, "send_email": True})).json()
    assert issued["email"] == "queued"
    await asyncio.sleep(0.3)
    [mail] = smtp.to(REQUESTER)
    assert mail.subject == "Din inbjudningskod till classbeat"
    assert issued["code"] in mail.text
    assert "/sv/signup" in mail.text


async def test_nothing_is_mailed_when_mail_is_off(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake
) -> None:
    sent: list[Any] = []

    class Off:
        """A mailer with no SMTP host (``CLASSBEAT_SMTP_HOST`` unset), recording any send."""

        configured = False

        async def send(self, message: Any) -> None:
            sent.append(message)

    env.app.state.mailer = Off()
    actor = await stepped_admin(env, make_actor)
    body = {**TIER, "send_email": True}
    issued = (await actor.post(f"{PATH}/{add_request(superuser)}/approve", body)).json()
    await asyncio.sleep(0.2)
    assert issued["email"] == "mail_off"
    assert sent == []
    assert smtp.to(REQUESTER) == []


@pytest.fixture
def records() -> Any:
    """Everything the app logs while the test runs, as JSON lines (``log.JsonFormatter``)."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield stream
    root.removeHandler(handler)
    root.setLevel(level)


async def test_no_log_line_holds_the_code_or_the_address(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake, records: Any
) -> None:
    actor = await stepped_admin(env, make_actor)
    request_id = add_request(superuser)
    body = {**TIER, "send_email": True}
    issued = (await actor.post(f"{PATH}/{request_id}/approve", body)).json()
    await actor.get(PATH)
    await sign_up(env, issued["code"])
    await confirm(env, smtp)
    logged = records.getvalue().lower()
    assert issued["code"].lower() not in logged
    assert NORMALIZED not in logged
