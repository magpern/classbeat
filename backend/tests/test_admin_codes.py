"""The admin Codes screen (ADR 0011 sections 1, 2 and 6; AD2 #220): invites and redeem codes.

The gate on every route and the audit row of every call; an invite the admin creates signs a new
user up through the unchanged M1 flow and a disabled one is refused by it (also between sign-up
and confirmation); a code the admin creates redeems through PL1 and a disabled one is
``code_invalid``; the code is in the create response only (never a list, an audit row or a log).
Fakes only (the SMTP fake)."""

import hashlib
import io
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems
from classbeat.log import JsonFormatter
from classbeat.plans import codes as plan_codes
from flows import PASSWORD, Browser
from smtpfake import SmtpFake

INVITES = "/api/admin/invites"
CODES = "/api/admin/redeem-codes"
REDEEM = "/api/account/redeem"
NEW_USER = "new.user@example.com"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str, smtp: SmtpFake) -> AsyncIterator[Env]:
    """The app with the maint credentials and its mail going to the SMTP fake."""
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
    ) as env:
        yield env


def reason(response: Any) -> str:
    return response.json()["detail"]["reason"]


def audit(conn: Connection, actor_id: object) -> list[tuple[str, str, str | None]]:
    """``(action, outcome, reason)`` of ``actor_id``'s audit rows, oldest first."""
    return [
        (r.action, r.outcome, r.reason)
        for r in conn.execute(
            text(
                "SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :u "
                "ORDER BY created_at, id"
            ),
            {"u": actor_id},
        )
    ]


async def stepped_admin(env: Env, make_actor: Any, email: str = "a@example.com") -> Any:
    """An admin with TOTP in the last 12 hours and a fresh step-up."""
    user_id = await env.user(email, role="admin")
    actor = await make_actor(user_id, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


def code_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "label": "beta testers",
        "plan_id": "instructor",
        "features": ["song_picking"],
        "grant_days": 30,
        "max_redemptions": 5,
        "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    }
    return {**body, **overrides}


def routes_for(invite_id: object, code_id: object) -> list[tuple[str, str, Any]]:
    """Every Codes route as (method, path, body)."""
    return [
        ("GET", INVITES, None),
        ("POST", INVITES, {"expires_days": 3}),
        ("POST", f"{INVITES}/{invite_id}/disable", None),
        ("GET", CODES, None),
        ("POST", CODES, code_body()),
        ("POST", f"{CODES}/{code_id}/disable", None),
    ]


async def send(actor: Any, method: str, path: str, body: Any = None) -> Any:
    return await (actor.get(path) if method == "GET" else actor.call(method, path, body))


async def sign_up(env: Env, code: str) -> Any:
    return await Browser(env).post(
        "/api/auth/signup", {"email": NEW_USER, "password": PASSWORD, "invite_code": code}
    )


async def confirm(env: Env, smtp: SmtpFake) -> Any:
    token = smtp.to(NEW_USER)[-1].token()
    return await Browser(env).post(
        "/api/auth/signup/confirm", {"token": token, "password": PASSWORD}
    )


# --- the gate ---------------------------------------------------------------------------------


async def test_the_router_guard_covers_the_codes_routes(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {
        INVITES,
        f"{INVITES}/{{invite_id}}/disable",
        CODES,
        f"{CODES}/{{code_id}}/disable",
    } <= paths


async def test_a_non_admin_gets_403_on_every_route_and_no_audit_row(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    plain = await env.user("plain@example.com")
    actor = await make_actor(plain, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now()")
    for method, path, body in routes_for(uuid.uuid4(), uuid.uuid4()):
        response = await send(actor, method, path, body)
        assert (response.status_code, reason(response)) == (403, "forbidden"), path
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM redeem_codes")).scalar_one() == 0


async def test_mfa_and_stepup_refusals_are_audited_and_change_nothing(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    admin = await env.user("a@example.com", role="admin")
    no_mfa = await make_actor(admin)
    for method, path, body in routes_for(uuid.uuid4(), uuid.uuid4()):
        response = await send(no_mfa, method, path, body)
        assert (response.status_code, reason(response)) == (403, "mfa_required"), path
    no_stepup = await make_actor(admin, mfa=True)
    writes = [r for r in routes_for(uuid.uuid4(), uuid.uuid4()) if r[0] != "GET"]
    for method, path, body in writes:
        response = await send(no_stepup, method, path, body)
        assert (response.status_code, reason(response)) == (403, "stepup_required"), path
        assert response.headers["cache-control"] == "no-store"
    assert audit(superuser, admin) == [("admin.request", "refused", "mfa_required")] * 6 + [
        ("admin.request", "refused", "stepup_required")
    ] * len(writes)
    assert superuser.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0
    assert superuser.execute(text("SELECT count(*) FROM redeem_codes")).scalar_one() == 0


async def test_unknown_ids_are_404_and_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    ghost = uuid.uuid4()
    for path in (f"{INVITES}/{ghost}/disable", f"{CODES}/{ghost}/disable"):
        response = await actor.post(path)
        assert (response.status_code, reason(response)) == (404, "not_found"), path
        assert response.headers["cache-control"] == "no-store"
    assert audit(superuser, actor.user_id) == [
        ("invite.disable", "refused", "not_found"),
        ("code.disable", "refused", "not_found"),
    ]
    kinds = superuser.execute(
        text("SELECT target_kind, target_id FROM admin_audit ORDER BY created_at, id")
    ).all()
    assert [tuple(k) for k in kinds] == [("invite", str(ghost)), ("redeem_code", str(ghost))]
    # A malformed id is a schema refusal.
    assert (await actor.post(f"{INVITES}/not-a-uuid/disable")).status_code == 422


# --- invites ----------------------------------------------------------------------------------


async def test_a_created_invite_signs_a_new_user_up_through_the_existing_flow(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.post(INVITES, {"expires_days": 3})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    code, invite = body["code"], body["invite"]
    assert invite["state"] == "active"
    assert invite["created_by"] == {"id": str(actor.user_id), "email": "a@example.com"}
    assert invite["used_by"] is None
    row = superuser.execute(
        text("SELECT code_hash, created_by, expires_at - created_at AS span FROM invites")
    ).one()
    assert row.code_hash == hashlib.sha256(code.encode()).digest()  # the CLI's hashing
    assert (row.created_by, row.span) == (actor.user_id, timedelta(days=3))
    # The unchanged M1 sign-up: pending row, confirmation mail, account created.
    assert (await sign_up(env, code)).status_code == 202
    confirmed = await confirm(env, smtp)
    assert (confirmed.status_code, confirmed.json()) == (200, {"status": "created"})
    new_user = superuser.execute(
        text("SELECT id FROM users WHERE email_normalized = :e"), {"e": NEW_USER}
    ).scalar_one()
    listed = (await actor.get(INVITES)).json()
    assert listed["next_offset"] is None
    [shown] = listed["invites"]
    assert shown["state"] == "used"
    assert shown["used_by"] == {"id": str(new_user), "email": NEW_USER}
    assert shown["used_at"] is not None
    params = superuser.execute(
        text("SELECT params FROM admin_audit WHERE action = 'invite.create'")
    ).scalar_one()
    assert params == {"expires_days": 3}
    assert audit(superuser, actor.user_id) == [
        ("invite.create", "ok", None),
        ("invite.list", "ok", None),
    ]
    # A used invite can't be disabled; nothing changes.
    again = await actor.post(f"{INVITES}/{invite['id']}/disable")
    assert (again.status_code, reason(again)) == (409, "invite_used")


async def test_a_disabled_invite_is_refused_by_the_m1_paths(
    env: Env, make_actor: Any, superuser: Connection, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    first = (await actor.post(INVITES, {"expires_days": 7})).json()
    disabled = await actor.post(f"{INVITES}/{first['invite']['id']}/disable")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["state"] == "expired"
    assert superuser.execute(
        text("SELECT expires_at <= now() FROM invites WHERE id = :i"), {"i": first["invite"]["id"]}
    ).scalar_one()
    refused = await sign_up(env, first["code"])
    assert (refused.status_code, reason(refused)) == (400, "invalid_invite")
    # Disabled between sign-up and confirmation: the consume step refuses it.
    second = (await actor.post(INVITES, {})).json()
    assert second["invite"]["expires_at"] > second["invite"]["created_at"]
    assert (await sign_up(env, second["code"])).status_code == 202
    assert (await actor.post(f"{INVITES}/{second['invite']['id']}/disable")).status_code == 200
    late = await confirm(env, smtp)
    assert (late.status_code, reason(late)) == (409, "invite_invalid")
    assert (
        superuser.execute(text("SELECT count(*) FROM users WHERE role = 'user'")).scalar_one() == 0
    )
    again = await actor.post(f"{INVITES}/{second['invite']['id']}/disable")
    assert (again.status_code, reason(again)) == (409, "already_expired")
    assert [a for a in audit(superuser, actor.user_id) if a[0] == "invite.disable"] == [
        ("invite.disable", "ok", None),
        ("invite.disable", "ok", None),
        ("invite.disable", "refused", "already_expired"),
    ]


async def test_invite_input_is_bounded(env: Env, make_actor: Any, superuser: Connection) -> None:
    actor = await stepped_admin(env, make_actor)
    for body in ({"expires_days": 0}, {"expires_days": 366}, {"expires_days": 3, "code": "x"}):
        assert (await actor.post(INVITES, body)).status_code == 422, body
    assert superuser.execute(text("SELECT count(*) FROM invites")).scalar_one() == 0


async def test_the_invite_list_pages_newest_first(env: Env, make_actor: Any) -> None:
    actor = await stepped_admin(env, make_actor)
    ids = [
        (await actor.post(INVITES, {"expires_days": 1})).json()["invite"]["id"] for _ in range(3)
    ]
    page = (await actor.get(f"{INVITES}?limit=2")).json()
    assert [i["id"] for i in page["invites"]] == ids[::-1][:2]
    assert page["next_offset"] == 2
    rest = (await actor.get(f"{INVITES}?offset=2&limit=2")).json()
    assert [i["id"] for i in rest["invites"]] == ids[:1]
    assert rest["next_offset"] is None
    assert (await actor.get(f"{INVITES}?limit=101")).status_code == 422


# --- redeem codes -----------------------------------------------------------------------------


async def test_a_created_code_redeems_through_pl1_and_a_disabled_one_is_code_invalid(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.post(CODES, code_body(max_redemptions=3))
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    created = response.json()
    code, shown = created["code"], created["redeem_code"]
    assert len(code) == plan_codes.CODE_LENGTH
    assert (shown["plan_id"], shown["features"], shown["grant_days"]) == (
        "instructor",
        ["song_picking"],
        30,
    )
    assert (shown["max_redemptions"], shown["redeemed_count"], shown["state"]) == (3, 0, "active")
    assert shown["created_by"] == {"id": str(actor.user_id), "email": "a@example.com"}
    stored = superuser.execute(
        text("SELECT code_hash, created_by, label FROM redeem_codes WHERE id = :i"),
        {"i": shown["id"]},
    ).one()
    assert stored.code_hash == plan_codes.hash_typed(code)
    assert (stored.created_by, stored.label) == (actor.user_id, "beta testers")

    alice = await make_actor(await env.user("alice@example.com"))
    redeemed = await alice.post(REDEEM, {"code": code})
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["plan"] == "instructor"
    [listed] = (await actor.get(CODES)).json()["redeem_codes"]
    assert (listed["id"], listed["redeemed_count"]) == (shown["id"], 1)

    disabled = await actor.post(f"{CODES}/{shown['id']}/disable")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["state"] == "disabled"
    assert disabled.json()["disabled_at"] is not None
    bob = await make_actor(await env.user("bob@example.com"))
    refused = await bob.post(REDEEM, {"code": code})
    assert (refused.status_code, reason(refused)) == (400, "code_invalid")
    again = await actor.post(f"{CODES}/{shown['id']}/disable")
    assert (again.status_code, reason(again)) == (409, "already_disabled")
    # The disable left the count alone.
    assert (
        superuser.execute(
            text("SELECT redeemed_count FROM redeem_codes WHERE id = :i"), {"i": shown["id"]}
        ).scalar_one()
        == 1
    )
    assert audit(superuser, actor.user_id) == [
        ("code.create", "ok", None),
        ("code.list", "ok", None),
        ("code.disable", "ok", None),
        ("code.disable", "refused", "already_disabled"),
    ]
    params = superuser.execute(
        text("SELECT params FROM admin_audit WHERE action = 'code.create'")
    ).scalar_one()
    assert set(params) == {"plan_id", "features", "grant_days", "max_redemptions", "expires_at"}


async def test_the_code_list_shows_each_state(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    made = {}
    for name in ("active", "used_up", "expired", "disabled"):
        made[name] = (await actor.post(CODES, code_body(label=name, max_redemptions=1))).json()
    alice = await make_actor(await env.user("alice@example.com"))
    assert (await alice.post(REDEEM, {"code": made["used_up"]["code"]})).status_code == 200
    superuser.execute(
        text("UPDATE redeem_codes SET expires_at = now() - interval '1 minute' WHERE id = :i"),
        {"i": made["expired"]["redeem_code"]["id"]},
    )
    assert (await actor.post(f"{CODES}/{made['disabled']['redeem_code']['id']}/disable")).is_success
    page = (await actor.get(CODES)).json()
    assert {c["label"]: c["state"] for c in page["redeem_codes"]} == {
        "active": "active",
        "used_up": "used_up",
        "expired": "expired",
        "disabled": "disabled",
    }
    assert [c["label"] for c in page["redeem_codes"]] == [
        "disabled",
        "expired",
        "used_up",
        "active",
    ]
    two = (await actor.get(f"{CODES}?offset=1&limit=2")).json()
    assert [c["label"] for c in two["redeem_codes"]] == ["expired", "used_up"]
    assert two["next_offset"] == 3


@pytest.mark.parametrize(
    "overrides",
    [
        {"plan_id": None, "features": []},  # grants nothing
        {"features": ["swedebeat"]},  # not a feature
        {"features": ["song_picking", "song_picking"]},
        {"grant_days": 0},
        {"grant_days": 367},
        {"max_redemptions": 0},
        {"max_redemptions": 1001},
        {"label": ""},
        {"label": " "},
        {"label": "x" * 81},
        {"label": "a\nb"},
        {"expires_at": "2030-01-01T00:00:00"},  # no time zone
        {"plan_id": "Not A Plan"},
        {"code": "AAAA"},  # the admin can't choose the code
        {"redeemed_count": 3},
    ],
)
async def test_code_input_is_validated(
    overrides: dict[str, Any], env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.post(CODES, code_body(**overrides))
    assert response.status_code == 422, response.text
    assert superuser.execute(text("SELECT count(*) FROM redeem_codes")).scalar_one() == 0
    # A schema refusal is audited as an input refusal only (#227): no code.create row, no code.
    assert audit(superuser, actor.user_id) == [("admin.request", "refused", "invalid_input")]


async def test_an_unknown_plan_and_a_past_expiry_are_refused_and_audited(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    unknown = await actor.post(CODES, code_body(plan_id="platinum"))
    assert (unknown.status_code, reason(unknown)) == (422, "unknown_plan")
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    expired = await actor.post(CODES, code_body(expires_at=past))
    assert (expired.status_code, reason(expired)) == (422, "expires_in_past")
    features_only = await actor.post(CODES, code_body(plan_id=None))
    assert features_only.status_code == 201, features_only.text
    assert features_only.json()["redeem_code"]["plan_id"] is None
    assert superuser.execute(text("SELECT count(*) FROM redeem_codes")).scalar_one() == 1
    assert audit(superuser, actor.user_id) == [
        ("code.create", "refused", "unknown_plan"),
        ("code.create", "refused", "expires_in_past"),
        ("code.create", "ok", None),
    ]


# --- the code is shown once -------------------------------------------------------------------


async def test_codes_never_reach_a_list_an_audit_row_or_a_log(
    env: Env, make_actor: Any, superuser: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    actor = await stepped_admin(env, make_actor)
    invite = (await actor.post(INVITES, {"expires_days": 2})).json()
    code = (await actor.post(CODES, code_body())).json()
    planted = [invite["code"], code["code"]]
    hashes = [
        hashlib.sha256(invite["code"].encode()).hexdigest(),
        (plan_codes.hash_typed(code["code"]) or b"").hex(),
    ]
    bodies = [
        (await actor.get(INVITES)).text,
        (await actor.get(CODES)).text,
        (await actor.post(f"{INVITES}/{invite['invite']['id']}/disable")).text,
        (await actor.post(f"{CODES}/{code['redeem_code']['id']}/disable")).text,
    ]
    audit_text = " ".join(
        str(tuple(r))
        for r in superuser.execute(
            text("SELECT action, target_kind, target_id, params::text, reason FROM admin_audit")
        )
    )
    out = io.StringIO()
    formatter = JsonFormatter()
    for record in caplog.records:
        out.write(formatter.format(record) + "\n")
    logged = out.getvalue() + caplog.text
    assert caplog.records  # the access log at least
    for value in planted:
        for form in (value, value.lower(), value[:12], value[-12:]):
            assert all(form not in body for body in bodies), form
            assert form not in audit_text
            assert form not in logged
    for digest in hashes:
        assert all(digest not in body for body in bodies)
        assert digest not in audit_text
    # No list field could carry one.
    assert "code" not in (await actor.get(INVITES)).json()["invites"][0]
    assert "code_hash" not in (await actor.get(CODES)).json()["redeem_codes"][0]
