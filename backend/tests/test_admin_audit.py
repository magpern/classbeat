"""The admin audit-log view (ADR 0011 sections 1, 5 and 6; AD4 #222): ``GET /api/admin/audit``
filters by actor, action, target, outcome and period, newest first, paged by a cursor; the read
is audited once with its filter; a row shows ADR 0011 section 1's columns but never ``ip_hash``;
the view is read only and neither the app role nor ``classbeat_maint`` can change an audit row
(T92). Fakes only."""

import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError

from admintools import new_admin
from authtools import Env, running_env
from classbeat.admin import audit_routes
from classbeat.admin.audit import (
    ACTIONS,
    MAX_PARAMS_BYTES,
    Target,
    UnknownAction,
    checked_params,
    json_bytes,
)

AUDIT = "/api/admin/audit"
ROW_KEYS = {
    "id", "created_at", "actor_id", "actor_email", "action", "target_user_id", "target_kind",
    "target_id", "params", "outcome", "reason", "request_id",
}  # fmt: skip


def _maint_settings(maint_database_url: str) -> dict[str, object]:
    url = make_url(maint_database_url)
    return {
        "maint_database_url": url.set(password=None).render_as_string(hide_password=False),
        "database_maint_password": url.password,
    }


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    async with running_env(app_database_url, **_maint_settings(maint_database_url)) as env:
        yield env


@pytest.fixture
async def admin(env: Env, make_actor: Any) -> tuple[Any, UUID]:
    """A signed-in admin with TOTP in the last 12 hours, and their user id."""
    user_id = await env.user(f"admin-{secrets.token_hex(4)}@example.com", role="admin")
    return await make_actor(user_id, mfa=True), user_id


def plant(conn: Connection, *, hours_ago: float = 1, **columns: Any) -> UUID:
    """An audit row as the superuser (setup only), ``hours_ago`` before now."""
    values: dict[str, Any] = {
        "actor_email": "someone@example.test", "action": "user.read", "outcome": "ok",
        "request_id": f"req-{secrets.token_hex(6)}", "ip_hash": secrets.token_bytes(32),
        "params": "{}", **columns,
    }  # fmt: skip
    names = ", ".join(values)
    params = ", ".join(f"CAST(:{k} AS jsonb)" if k == "params" else f":{k}" for k in values)
    return conn.execute(
        text(
            f"INSERT INTO admin_audit (created_at, {names}) "  # noqa: S608
            f"VALUES (now() - make_interval(secs => :ago), {params}) RETURNING id"
        ),
        {**values, "ago": hours_ago * 3600},
    ).scalar_one()


async def page(actor: Any, **query: Any) -> Any:
    response = await actor.get(AUDIT + ("?" + urlencode(query) if query else ""))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def ids(body: Any, *, planted_only: set[UUID] | None = None) -> list[UUID]:
    found = [UUID(r["id"]) for r in body["rows"]]
    return [i for i in found if planted_only is None or i in planted_only]


def new_user(conn: Connection, email: str, role: str = "user") -> UUID:
    return conn.execute(
        text(
            "INSERT INTO users (email, email_normalized, email_verified_at, role) "
            "VALUES (:e, :e, now(), :r) RETURNING id"
        ),
        {"e": email, "r": role},
    ).scalar_one()


# --- filters, order, paging -------------------------------------------------------------------


async def test_rows_come_newest_first_and_every_filter_narrows_them(
    admin: Any, superuser: Connection
) -> None:
    actor, admin_id = admin
    other = new_user(superuser, "other-admin@example.test", "admin")
    target = new_user(superuser, "target@example.test")
    a = plant(
        superuser,
        hours_ago=5,
        actor_id=other,
        actor_email="other-admin@example.test",
        action="user.grant",
        target_user_id=target,
        params='{"plan_id": "pro"}',
    )
    b = plant(
        superuser,
        hours_ago=4,
        actor_id=other,
        actor_email="other-admin@example.test",
        action="user.read",
        target_user_id=target,
    )
    c = plant(
        superuser,
        hours_ago=3,
        actor_email="Gone@Example.test",
        action="code.disable",
        target_kind="redeem_code",
        target_id="c-1",
    )
    d = plant(
        superuser,
        hours_ago=2,
        actor_id=other,
        actor_email="other-admin@example.test",
        action="admin.request",
        outcome="refused",
        reason="stepup_required",
        params='{"method": "POST", "route": "/api/admin/users"}',
    )
    e = plant(superuser, hours_ago=30, actor_email="gone@example.test", action="user.delete",
              target_kind="user", target_id=str(uuid4()))  # fmt: skip
    planted = {a, b, c, d, e}

    body = await page(actor)
    assert ids(body, planted_only=planted) == [d, c, b, a, e]
    newest = body["rows"][0]  # this read's own audit row, written first in its transaction
    assert (newest["action"], newest["actor_id"], newest["outcome"]) == (
        "audit.list",
        str(admin_id),
        "ok",
    )
    for query, expected in (
        ({"actor_id": str(other)}, [d, b, a]),
        ({"actor_email": "GONE@example.test"}, [c, e]),
        ({"action": "user.read"}, [b]),
        ({"action": "user."}, [b, a, e]),
        ({"target_user_id": str(target)}, [b, a]),
        ({"target_kind": "redeem_code", "target_id": "c-1"}, [c]),
        ({"outcome": "refused"}, [d]),
        ({"since": iso(superuser, 4.5), "until": iso(superuser, 2.5)}, [c, b]),
        ({"actor_id": str(other), "action": "user.", "since": iso(superuser, 4.5)}, [b]),
    ):
        assert ids(await page(actor, **query), planted_only=planted) == expected, query
    assert (await page(actor, action="user.grant"))["rows"][0]["params"] == {"plan_id": "pro"}


def iso(conn: Connection, hours_ago: float) -> str:
    return (
        conn.execute(text("SELECT now() - make_interval(secs => :s)"), {"s": hours_ago * 3600})
        .scalar_one()
        .isoformat()
    )


async def test_the_cursor_pages_through_without_gaps_while_the_log_grows(
    admin: Any, superuser: Connection
) -> None:
    actor, _ = admin
    planted = [plant(superuser, hours_ago=10 - i, action="user.read") for i in range(7)]
    seen: list[UUID] = []
    body = await page(actor, action="user.read", limit=3)
    while True:
        seen += ids(body)
        plant(superuser, hours_ago=0, action="user.read")  # newer rows never shift a page
        if body["next_before"] is None:
            break
        body = await page(actor, action="user.read", limit=3, before=body["next_before"])
    assert seen == list(reversed(planted))
    assert len(set(seen)) == len(seen)


async def test_an_unknown_cursor_is_404_and_audited_refused(
    admin: Any, superuser: Connection
) -> None:
    actor, admin_id = admin
    response = await actor.get(f"{AUDIT}?before={uuid4()}")
    assert (response.status_code, response.json()) == (404, {"detail": {"reason": "not_found"}})
    found = superuser.execute(
        text("SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :a"),
        {"a": admin_id},
    ).all()
    assert [tuple(r) for r in found] == [("audit.list", "refused", "not_found")]


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 101},
        {"actor_id": "not-a-uuid"},
        {"before": "x"},
        {"action": "User.Read"},
        {"action": "user read"},
        {"target_kind": "Users"},
        {"target_id": "x" * 101},
        {"target_id": "a\x00b"},  # NUL: jsonb refuses it, so it never reaches a row
        {"actor_email": "a\x00b@example.test"},
        {"actor_email": "a\x1bb@example.test"},  # any control character (Cc)
        {"target_id": "a\x7fb"},
        {"target_id": "é" * 100},  # 100 characters, 600 bytes once JSON-encoded
        {"actor_email": "é" * 320},  # 320 characters, 1920 bytes once encoded
        {"actor_email": "\U0001f600" * 60},  # 12 bytes each once encoded
        {"outcome": "maybe"},
        {"since": "2026-10-01T00:00:00"},  # no time zone
        {"since": "2026-10-02T00:00:00Z", "until": "2026-10-01T00:00:00Z"},
        {"since": "2026-10-01T00:00:00Z", "until": "2026-10-01T00:00:00Z"},
    ],
)
async def test_a_bad_filter_is_422_and_writes_no_ok_row(
    admin: Any, superuser: Connection, query: dict[str, Any]
) -> None:
    actor, admin_id = admin
    response = await actor.get(f"{AUDIT}?{urlencode(query)}")
    assert response.status_code == 422, response.text
    assert (
        superuser.execute(
            text("SELECT count(*) FROM admin_audit WHERE actor_id = :a AND outcome = 'ok'"),
            {"a": admin_id},
        ).scalar_one()
        == 0
    )


# --- what a read records and shows -------------------------------------------------------------


async def test_a_read_is_audited_once_with_its_filter(admin: Any, superuser: Connection) -> None:
    actor, admin_id = admin
    target = new_user(superuser, "target@example.test")
    await page(actor, action="user.", target_user_id=str(target), actor_email=" A@Example.TEST ",
               limit=7)  # fmt: skip
    [row] = superuser.execute(
        text("SELECT action, outcome, params FROM admin_audit WHERE actor_id = :a"),
        {"a": admin_id},
    ).all()
    assert (row.action, row.outcome) == ("audit.list", "ok")
    unset = ("actor_id", "target_kind", "target_id", "outcome", "since", "until", "before")
    assert row.params == {
        "action": "user.",
        "actor_email": "a@example.test",  # trimmed and lowered, as the filter applies it
        "limit": 7,
        "target_user_id": str(target),
        **dict.fromkeys(unset),
    }


async def test_a_row_shows_only_the_readable_columns_and_never_the_ip_hash(
    admin: Any, superuser: Connection
) -> None:
    actor, _ = admin
    ip_hash = secrets.token_bytes(32)
    planted = plant(superuser, ip_hash=ip_hash, action="user.disable")
    body = await page(actor, action="user.disable")
    [row] = body["rows"]
    assert row["id"] == str(planted)
    assert set(row) == ROW_KEYS
    response = await actor.get(f"{AUDIT}?action=user.disable")
    for form in (ip_hash.hex(), ip_hash.hex().upper()):
        assert form not in response.text
    import base64

    assert base64.b64encode(ip_hash).decode() not in response.text


@pytest.mark.parametrize(
    ("email", "target_id"),
    [
        ("a" * 320, "t" * 100),  # the most characters
        ('"' * 320, "\\" * 100),  # two bytes each once escaped: exactly the byte bounds
        ("é" * 106, "é" * 33),  # six bytes each: just inside the byte bounds
        ("\U0001f600" * 53, "\U0001f600" * 16),  # twelve bytes each
    ],
)
def test_the_largest_accepted_filter_fits_the_audit_row(email: str, target_id: str) -> None:
    """Measured in encoded bytes, not characters: every value the filter accepts fits
    ``MAX_PARAMS_BYTES`` with every other field at its maximum."""
    assert "audit.list" in ACTIONS
    assert json_bytes(email) <= 640
    assert json_bytes(target_id) <= 200
    full = {
        "actor_id": str(uuid4()), "actor_email": email, "action": "a" * 60,
        "target_user_id": str(uuid4()), "target_kind": "k" * 40, "target_id": target_id,
        "outcome": "refused", "since": "2026-10-01T00:00:00.123456+14:00",
        "until": "2026-10-02T00:00:00.123456-12:00", "before": str(uuid4()), "limit": 100,
    }  # fmt: skip
    assert len(checked_params("audit.list", full).encode()) <= MAX_PARAMS_BYTES


def test_checked_params_never_returns_a_value_jsonb_refuses() -> None:
    with pytest.raises(UnknownAction):
        checked_params("admin.request", {"method": "GET", "route": "/api/admin/a\x00"})
    with pytest.raises(UnknownAction):
        checked_params("audit.list", {"target_id": "a\x00b"})


async def test_a_failure_whose_params_the_table_refuses_still_writes_its_row(
    env: Env, superuser: Connection
) -> None:
    """The error path does not depend on the params that may have caused the error: a row the
    table refuses with them is written without them (#282 review)."""
    from classbeat.admin import db as admin_db

    actor = new_admin(superuser)
    poisoned = '{"target_id": "a\\u0000b"}'  # what a NUL becomes once encoded
    await admin_db._record_failure(  # pyright: ignore[reportPrivateUsage]
        env.app.state.admin_db, actor, "audit.list", Target(), poisoned, RuntimeError("boom")
    )
    rows = superuser.execute(
        text("SELECT outcome, reason, params FROM admin_audit WHERE request_id = :r"),
        {"r": actor.request_id},
    ).all()
    assert [tuple(r) for r in rows] == [("error", "internal", {})]


async def test_since_is_inclusive_and_until_exclusive(admin: Any, superuser: Connection) -> None:
    actor, _ = admin
    at_since = plant(superuser, hours_ago=3, action="code.create")
    inside = plant(superuser, hours_ago=2, action="code.create")
    at_until = plant(superuser, hours_ago=1, action="code.create")
    since, until = (
        superuser.execute(
            text("SELECT created_at FROM admin_audit WHERE id = :i"), {"i": i}
        ).scalar_one().isoformat()
        for i in (at_since, at_until)
    )  # fmt: skip
    body = await page(actor, action="code.create", since=since, until=until)
    assert ids(body) == [inside, at_since]


async def test_rows_with_the_same_instant_are_paged_by_id_without_a_gap(
    admin: Any, superuser: Connection
) -> None:
    """One transaction's rows share ``created_at`` (``now()``); the cursor's ``id`` half keeps a
    page boundary between them from skipping or repeating one."""
    actor, _ = admin
    instant = superuser.execute(text("SELECT now() - interval '1 hour'")).scalar_one()
    planted = [
        superuser.execute(
            text(
                "INSERT INTO admin_audit (created_at, actor_email, action, outcome, request_id, "
                "ip_hash) VALUES (:t, 'x@example.test', 'user.enable', 'ok', 'r', :h) "
                "RETURNING id"
            ),
            {"t": instant, "h": secrets.token_bytes(32)},
        ).scalar_one()
        for _ in range(4)
    ]
    seen: list[UUID] = []
    body = await page(actor, action="user.enable", limit=1)
    while True:
        seen += ids(body)
        if body["next_before"] is None:
            break
        body = await page(actor, action="user.enable", limit=1, before=body["next_before"])
    assert seen == sorted(planted, key=str, reverse=True)


# --- the gate ----------------------------------------------------------------------------------


async def test_a_non_admin_gets_403_and_an_admin_without_totp_is_refused(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    plain = await make_actor(await env.user("plain@example.com"), mfa=True)
    response = await plain.get(AUDIT)
    assert (response.status_code, response.json()["detail"]["reason"]) == (403, "forbidden")
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 0
    admin_id = await env.user("a@example.com", role="admin")
    no_totp = await make_actor(admin_id)
    response = await no_totp.get(AUDIT)
    assert (response.status_code, response.json()["detail"]["reason"]) == (403, "mfa_required")
    rows = superuser.execute(
        text("SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :a"),
        {"a": admin_id},
    ).all()
    assert [tuple(r) for r in rows] == [("admin.request", "refused", "mfa_required")]


# --- read only: no route writes the log, and no role may change a row ------------------------


def test_the_audit_router_has_only_get_routes() -> None:
    routes = [r for r in audit_routes.router.routes if isinstance(r, APIRoute)]
    assert [(r.path, sorted(r.methods or ())) for r in routes] == [("/api/admin/audit", ["GET"])]


async def test_writing_methods_on_the_audit_path_are_not_routes(
    admin: Any, superuser: Connection
) -> None:
    actor, _ = admin
    superuser.execute(text("UPDATE sessions SET stepup_at = now()"))  # step-up is no way in
    before = superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = await actor.call(method, AUDIT, json={})
        assert response.status_code == 405, (method, response.text)
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == before


@pytest.fixture
def maint_engine(maint_database_url: str) -> Iterator[Engine]:
    engine = create_engine(maint_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def app_engine(app_database_url: str) -> Iterator[Engine]:
    engine = create_engine(app_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


CHANGES = [
    "UPDATE admin_audit SET outcome = 'error' WHERE id = :i",
    "UPDATE admin_audit SET params = '{}' WHERE id = :i",
    "UPDATE admin_audit SET actor_id = NULL WHERE id = :i",
    "DELETE FROM admin_audit WHERE id = :i",
    "TRUNCATE admin_audit",
    "SELECT ip_hash FROM admin_audit WHERE id = :i",
]


@pytest.mark.parametrize("statement", CHANGES)
def test_t92_maint_cannot_change_or_delete_an_audit_row(
    superuser: Connection, maint_engine: Engine, statement: str
) -> None:
    actor = new_user(superuser, "a@example.test", "admin")
    planted = plant(superuser, actor_id=actor, outcome="ok")
    with maint_engine.begin() as conn:
        conn.execute(text("SET TRANSACTION READ WRITE"))  # so the refusal is the grant's
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text(statement), {"i": planted})
    kept = superuser.execute(
        text("SELECT outcome, actor_id, params FROM admin_audit WHERE id = :i"), {"i": planted}
    ).one()
    assert (kept.outcome, kept.actor_id, kept.params) == ("ok", actor, {})


@pytest.mark.parametrize(
    "statement", [*CHANGES, "SELECT id FROM admin_audit", "INSERT INTO admin_audit "
     "(actor_email, action, outcome, request_id, ip_hash) VALUES ('x@example.test', "
     "'user.read', 'ok', 'r', sha256('x'))"],
)  # fmt: skip
def test_t92_the_app_role_can_neither_read_nor_change_the_log(
    superuser: Connection, app_engine: Engine, statement: str
) -> None:
    planted = plant(superuser)
    with pytest.raises(DBAPIError, match="permission denied"), app_engine.begin() as conn:
        conn.execute(text(statement), {"i": planted})
    assert superuser.execute(text("SELECT count(*) FROM admin_audit")).scalar_one() == 1


def test_t92_maint_may_only_append(superuser: Connection, maint_engine: Engine) -> None:
    privileges = {
        p: superuser.execute(
            text("SELECT has_table_privilege('classbeat_maint', 'public.admin_audit', :p)"),
            {"p": p},
        ).scalar_one()
        for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    }
    assert privileges == {"INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False,
                          "REFERENCES": False, "TRIGGER": False}  # fmt: skip
    readable = superuser.execute(
        text(
            "SELECT has_column_privilege('classbeat_maint', 'public.admin_audit', 'ip_hash', "
            "'SELECT')"
        )
    ).scalar_one()
    assert readable is False
    # The view's own columns are exactly what maint may read of the table.
    columns = (
        superuser.execute(
            text(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'public.admin_audit'::regclass "
                "AND attnum > 0 AND NOT attisdropped "
                "AND has_column_privilege('classbeat_maint', attrelid, attnum, 'SELECT')"
            )
        )
        .scalars()
        .all()
    )
    assert set(columns) == ROW_KEYS
