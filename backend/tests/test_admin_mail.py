"""The admin Mail page (ADR 0022 sections 2, 5 and 6; ME2 #653).

The gate on every route, the branding and switch settings and their audit rows (key plus length
or kind ids, never the text), the preview with its own CSP and the path-scoped exemption in
``SecurityHeaders`` (success plus the four errors), the test send (own address, 10 an hour), and
that a disabled kind is not sent but can still be previewed and test-sent. Fakes only."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from adminviews import reason, stepped_admin
from authtools import Env, running_env
from classbeat.admin.routes import admin_area_routes, gate_problems
from classbeat.emails import live
from classbeat.emails.registry import REGISTRY
from classbeat.mail import Mailer, MailError
from smtpfake import SmtpFake

MAIL = "/api/admin/mail"
SETTINGS = "/api/admin/settings"
ESSENTIAL = "password_reset"
OPTIONAL = "totp_enrolled"
SECRET_TEXT = "Sender-Planted-Text-93"


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str, smtp: SmtpFake) -> AsyncIterator[Env]:
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        smtp_host="127.0.0.1",
        smtp_port=smtp.port,
        smtp_tls="none",
    ) as running:
        yield running


@pytest.fixture(autouse=True)
def clean(superuser: Connection) -> Any:
    superuser.execute(text("DELETE FROM app_settings"))
    yield
    superuser.execute(text("DELETE FROM app_settings"))


def audit_rows(conn: Connection, actor_id: object) -> list[Any]:
    return list(
        conn.execute(
            text(
                "SELECT action, outcome, reason, target_kind, target_id, params "
                "FROM admin_audit WHERE actor_id = :u ORDER BY created_at, id"
            ),
            {"u": actor_id},
        )
    )


async def put(actor: Any, key: str, value: Any) -> Any:
    return await actor.call("PUT", f"{SETTINGS}/{key}", {"value": value})


def routes() -> list[tuple[str, str, Any]]:
    return [
        ("GET", MAIL, None),
        ("GET", f"{MAIL}/{OPTIONAL}/preview", None),
        ("POST", f"{MAIL}/{OPTIONAL}/test", None),
    ]


async def send(actor: Any, method: str, path: str, body: Any = None) -> Any:
    return await (actor.get(path) if method == "GET" else actor.call(method, path, body))


# --- the gate ---------------------------------------------------------------------------------


async def test_the_router_guard_covers_the_mail_routes(env: Env) -> None:
    assert gate_problems(env.app.routes) == []
    paths = {path for path, _ in admin_area_routes(env.app.routes)}
    assert {MAIL, f"{MAIL}/{{kind_id}}/preview", f"{MAIL}/{{kind_id}}/test"} <= paths


async def test_anonymous_and_non_admin_and_unstepped_requests_are_refused(
    env: Env, make_actor: Any, smtp: SmtpFake
) -> None:
    for method, path, _body in routes():
        anonymous = await env.client.request(method, path)
        assert anonymous.status_code in (401, 403), path
    plain = await env.user("plain@example.com")
    actor = await make_actor(plain, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now()")
    for method, path, body in routes():
        response = await send(actor, method, path, body)
        assert (response.status_code, reason(response)) == (403, "forbidden"), path
    admin = await env.user("a@example.com", role="admin")
    no_mfa = await make_actor(admin)
    for method, path, body in routes():
        response = await send(no_mfa, method, path, body)
        assert (response.status_code, reason(response)) == (403, "mfa_required"), path
    no_stepup = await make_actor(admin, mfa=True)
    response = await send(no_stepup, "POST", f"{MAIL}/{OPTIONAL}/test")
    assert (response.status_code, reason(response)) == (403, "stepup_required")
    assert smtp.messages == []


# --- reading and the settings -----------------------------------------------------------------


async def test_the_read_lists_every_kind_and_the_four_settings(env: Env, make_actor: Any) -> None:
    actor = await stepped_admin(env, make_actor)
    body = (await actor.get(MAIL)).json()
    assert [f["key"] for f in body["settings"]] == [
        "mail_sender_name",
        "mail_support_email",
        "mail_footer",
        "mail_kinds_disabled",
    ]
    by_id = {k["id"]: k for k in body["kinds"]}
    assert set(by_id) == set(REGISTRY)
    assert by_id[ESSENTIAL]["essential"] is True
    assert by_id[ESSENTIAL]["admin_can_disable"] is False
    assert by_id[OPTIONAL]["admin_can_disable"] is True
    assert all(k["enabled"] for k in body["kinds"])


async def test_branding_is_saved_shown_and_applied_to_a_test_mail(
    env: Env, make_actor: Any, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    assert (await put(actor, "mail_sender_name", "Studio Beat")).status_code == 200
    assert (await put(actor, "mail_support_email", "help@example.com")).status_code == 200
    assert (await put(actor, "mail_footer", SECRET_TEXT)).status_code == 200
    shown = {f["key"]: f for f in (await actor.get(MAIL)).json()["settings"]}
    assert shown["mail_sender_name"]["effective"] == "Studio Beat"
    assert shown["mail_support_email"]["effective"] == "help@example.com"
    assert (await actor.call("POST", f"{MAIL}/{OPTIONAL}/test", None)).status_code == 200
    (message,) = smtp.to("a@example.com")
    assert "Studio Beat" in str(message.message["From"])
    assert "help@example.com" in message.html
    assert SECRET_TEXT in message.html


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("mail_sender_name", ""),
        ("mail_sender_name", "x" * 61),
        ("mail_sender_name", "line\nbreak"),
        ("mail_support_email", "not an address"),
        ("mail_support_email", "a@b.c, d@e.f"),
        ("mail_footer", "y" * 281),
        ("mail_kinds_disabled", "totp_enrolled"),
        ("mail_kinds_disabled", ["totp_enrolled", "totp_enrolled"]),
        ("mail_kinds_disabled", ["no_such_kind"]),
    ],
)
async def test_an_invalid_value_is_refused_and_changes_nothing(
    env: Env, make_actor: Any, superuser: Connection, key: str, value: Any
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await put(actor, key, value)
    assert response.status_code == 422
    assert superuser.execute(text("SELECT count(*) FROM app_settings")).scalar_one() == 0


async def test_an_essential_kind_cannot_be_disabled(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    for kind in (k.id for k in REGISTRY.values() if not k.admin_can_disable):
        response = await put(actor, "mail_kinds_disabled", [kind])
        assert response.status_code == 422, kind
        assert reason(response) == "essential_kind", kind
    unknown = await put(actor, "mail_kinds_disabled", ["no_such_kind"])
    assert reason(unknown) == "unknown_kind"
    assert superuser.execute(text("SELECT count(*) FROM app_settings")).scalar_one() == 0


async def test_a_stored_essential_kind_is_ignored_when_sending(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """A stale or planted row never silences a mail that must go out."""
    superuser.execute(
        text(
            "INSERT INTO app_settings (key, value) "
            "VALUES ('mail_kinds_disabled', CAST(:v AS jsonb))"
        ),
        {"v": json.dumps([ESSENTIAL, OPTIONAL])},
    )
    async with env.db() as db:
        config = await live.load(db, env.settings)
    assert config.disabled == frozenset({OPTIONAL})


async def test_the_audit_holds_the_key_and_a_length_or_the_kind_ids_only(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    await put(actor, "mail_footer", SECRET_TEXT)
    await put(actor, "mail_sender_name", "Studio Beat")
    await put(actor, "mail_support_email", "help@example.com")
    await put(actor, "mail_kinds_disabled", [OPTIONAL])
    await actor.call("DELETE", f"{SETTINGS}/mail_footer", None)
    rows = audit_rows(superuser, actor.user_id)
    dumped = json.dumps([dict(r._mapping) for r in rows], default=str)
    for planted in (SECRET_TEXT, "Studio Beat", "help@example.com"):
        assert planted not in dumped
    params = {r.params["key"]: r.params for r in rows if r.action == "setting.set"}
    assert params["mail_footer"]["text_length"] == len(SECRET_TEXT)
    assert params["mail_kinds_disabled"]["kinds"] == [OPTIONAL]


# --- the preview ------------------------------------------------------------------------------


async def test_every_kind_can_be_previewed_in_both_parts_with_its_own_policy(
    env: Env, make_actor: Any
) -> None:
    actor = await stepped_admin(env, make_actor)
    for kind_id in REGISTRY:
        for part in ("html", "text"):
            response = await actor.get(f"{MAIL}/{kind_id}/preview?part={part}")
            assert response.status_code == 200, (kind_id, part)
            policy = response.headers["content-security-policy"]
            assert policy == preview_policy_of(env)
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["cache-control"] == "no-store"
            expected = "text/html" if part == "html" else "text/plain"
            assert response.headers["content-type"].startswith(expected)


async def test_a_disabled_kind_is_not_sent_but_can_be_previewed_and_test_sent(
    env: Env, make_actor: Any, smtp: SmtpFake
) -> None:
    actor = await stepped_admin(env, make_actor)
    assert (await put(actor, "mail_kinds_disabled", [OPTIONAL])).status_code == 200
    kinds = {k["id"]: k for k in (await actor.get(MAIL)).json()["kinds"]}
    assert kinds[OPTIONAL]["enabled"] is False
    sent = await live.send_optional(
        env.factory,
        env.app.state.mailer,
        env.settings,
        OPTIONAL,
        "someone@example.com",
        {"when": datetime.now(UTC)},
    )
    assert sent is False
    assert smtp.to("someone@example.com") == []
    assert (await actor.get(f"{MAIL}/{OPTIONAL}/preview")).status_code == 200
    assert (await actor.call("POST", f"{MAIL}/{OPTIONAL}/test", None)).status_code == 200
    assert len(smtp.to("a@example.com")) == 1


async def test_the_notifier_respects_the_switch(env: Env, make_actor: Any, smtp: SmtpFake) -> None:
    from classbeat.auth.notifier import MailNotifier

    actor = await stepped_admin(env, make_actor)
    notifier = MailNotifier(env.app.state.mailer, env.settings, env.factory)
    await notifier.totp_enrolled(actor.user_id, "a@example.com")
    assert len(smtp.to("a@example.com")) == 1
    await put(actor, "mail_kinds_disabled", [OPTIONAL])
    await notifier.totp_enrolled(actor.user_id, "a@example.com")
    assert len(smtp.to("a@example.com")) == 1


async def test_an_unknown_kind_is_404_and_audited_as_refused(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.get(f"{MAIL}/no_such_kind/preview")
    assert (response.status_code, reason(response)) == (404, "not_found")
    assert [r.outcome for r in audit_rows(superuser, actor.user_id)][-1] == "refused"


# --- the test send ----------------------------------------------------------------------------


async def test_a_test_mail_goes_to_the_admins_own_address_only_and_is_audited(
    env: Env, make_actor: Any, smtp: SmtpFake, superuser: Connection
) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.call("POST", f"{MAIL}/{ESSENTIAL}/test", None)
    assert response.status_code == 200
    (message,) = smtp.messages
    assert message.to == "a@example.com"
    assert message.subject.startswith("[Test] ")
    rows = [r for r in audit_rows(superuser, actor.user_id) if r.action == "mail.test"]
    assert len(rows) == 1
    assert rows[0].params == {"kind": ESSENTIAL}
    assert "example.com" not in json.dumps(dict(rows[0]._mapping), default=str)


async def test_test_sends_are_limited_to_ten_an_hour(
    env: Env, make_actor: Any, smtp: SmtpFake, freeze_rate_windows: Any
) -> None:
    freeze_rate_windows(None)
    actor = await stepped_admin(env, make_actor)
    for _ in range(10):
        assert (await actor.call("POST", f"{MAIL}/{OPTIONAL}/test", None)).status_code == 200
    eleventh = await actor.call("POST", f"{MAIL}/{OPTIONAL}/test", None)
    assert (eleventh.status_code, reason(eleventh)) == (429, "rate_limited")
    assert len(smtp.messages) == 10


async def test_a_test_send_without_a_mail_server_is_409(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        env.app.state, "mailer", Mailer(env.settings.model_copy(update={"smtp_host": None}))
    )
    actor = await stepped_admin(env, make_actor)
    response = await actor.call("POST", f"{MAIL}/{OPTIONAL}/test", None)
    assert (response.status_code, reason(response)) == (409, "mail_not_configured")


async def test_a_refused_smtp_send_is_502(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*_: object, **__: object) -> None:
        raise MailError("refused")

    monkeypatch.setattr(env.app.state.mailer, "send", refuse)
    actor = await stepped_admin(env, make_actor)
    response = await actor.call("POST", f"{MAIL}/{OPTIONAL}/test", None)
    assert (response.status_code, reason(response)) == (502, "send_failed")


# --- the CSP exemption (ADR 0022 section 6) ---------------------------------------------------


def preview_policy_of(env: Env) -> str:
    """ADR 0022 section 6, written out so a widened directive fails."""
    base = env.settings.public_base_url
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; img-src {base}/email/; "
        "frame-ancestors 'self'; sandbox"
    )


def default_policy(env: Env) -> str:
    from classbeat.headers import content_security_policy

    return content_security_policy(env.settings.public_base_url)


async def test_the_app_policy_scopes_frame_src_to_the_preview_path(env: Env) -> None:
    response = await env.client.get("/api/health")
    policy = response.headers["content-security-policy"]
    assert f"frame-src {env.settings.public_base_url}/api/admin/mail/;" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["content-security-policy"] == default_policy(env)


async def test_a_successful_preview_keeps_its_own_policy(env: Env, make_actor: Any) -> None:
    actor = await stepped_admin(env, make_actor)
    response = await actor.get(f"{MAIL}/{OPTIONAL}/preview")
    policy = response.headers["content-security-policy"]
    assert policy == preview_policy_of(env)
    assert policy != default_policy(env)
    assert response.headers["referrer-policy"]


async def test_the_preview_errors_get_the_default_policy(
    env: Env, make_actor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = default_policy(env)
    # 401: no session.
    anonymous = await env.client.get(f"{MAIL}/{OPTIONAL}/preview")
    assert anonymous.status_code == 401
    assert anonymous.headers["content-security-policy"] == default
    # 403: a signed-in user who is not an admin.
    plain = await env.user("plain@example.com")
    plain_actor = await make_actor(plain, mfa=True)
    forbidden = await plain_actor.get(f"{MAIL}/{OPTIONAL}/preview")
    assert forbidden.status_code == 403
    assert forbidden.headers["content-security-policy"] == default
    # 404: an unknown kind.
    actor = await stepped_admin(env, make_actor)
    missing = await actor.get(f"{MAIL}/no_such_kind/preview")
    assert missing.status_code == 404
    assert missing.headers["content-security-policy"] == default

    # 500: the render raises. The unhandled-error handler sits outside the middleware.
    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("render failed")

    monkeypatch.setattr("classbeat.admin.mail.build", boom)
    # The test client re-raises an unhandled error; the real server answers 500.
    actor.client._transport = httpx.ASGITransport(app=env.app, raise_app_exceptions=False)
    crash = await actor.get(f"{MAIL}/{OPTIONAL}/preview")
    assert crash.status_code == 500
    assert crash.headers["content-security-policy"] == default
    assert "frame-ancestors 'none'" in crash.headers["content-security-policy"]


async def test_no_other_response_may_carry_a_policy_that_allows_framing(
    env: Env, make_actor: Any
) -> None:
    actor = await stepped_admin(env, make_actor)
    for path in ("/api/health", MAIL, SETTINGS, f"{MAIL}/{OPTIONAL}/previewx"):
        response = await actor.get(path)
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"], path
