"""M5 security suite, part 2 (#227): the admin area across **every** ``/api/admin`` route.

- The gate on every route, enumerated from the app (not a hand list): 403 ``forbidden`` with no
  audit row for a non-admin, ``mfa_required`` and (non-GET) ``stepup_required`` with exactly one
  ``refused`` row each.
- Input fuzz (PR #282 review): NUL, another control character and an over-long non-ASCII value
  in every path parameter, query parameter and top-level body field of every route. Each is a
  422 or 404 with exactly one audit row (``refused``), never a 500 and never an ``ok`` row.
- A planted value of every secret-bearing column of ADR 0011 section 5's list never appears in
  any admin response, the audit view's included (PR #277 review).

A route added later (AD2 #279 codes and invites, AD3 #281 usage, jobs and refunds) has no entry
in ``PATH_VALUES``/``BODIES`` until its PR adds one: the tests then fail and name it, so the fuzz
cannot silently skip a route.
"""

import json
import secrets
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, get_args, get_origin
from urllib.parse import quote, urlencode

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import Env, running_env
from classbeat import auth
from classbeat.admin.audit import RefusalLimiter
from classbeat.admin.routes import admin_area_routes

WHOAMI = "/api/admin/whoami"  # M1's route: require_admin only, no audit row (ADR 0003 s. 7)
BAD_VALUES = {
    "nul": "a\x00b",
    "control": "a\x1bb",
    "long_non_ascii": "é" * 2000,  # 2000 characters, 12000 bytes once JSON-encoded
}
# A valid request for every route, so each fuzzed field is the only thing wrong with it.
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/admin/users/{user_id}/entitlements"): {"plan_id": "pro"},
    ("DELETE", "/api/admin/users/{user_id}"): {"email": "wrong@example.com"},
}


@pytest.fixture
async def env(
    app_database_url: str, maint_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    """The app with maint, the admin rate limit off and the refusal-row limiter raised: this
    file sends far more than 60 requests and 20 refusals a minute, and every one must leave its
    row (the limits themselves are tested in ``test_admin_routes_guard.py``)."""
    url = make_url(maint_database_url)
    monkeypatch.setattr(auth, "admin_buckets", lambda _user_id: [])
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
    ) as env:
        env.app.state.admin_db.limiter = RefusalLimiter(per_admin=100_000, total=100_000)
        yield env


def routes(env: Env) -> list[tuple[str, APIRoute]]:
    """``(method, route)`` for every admin route but ``whoami``."""
    found = [
        (method, route)
        for path, route in admin_area_routes(env.app.routes)
        if path != WHOAMI and isinstance(route, APIRoute)
        for method in sorted(route.methods or ())
    ]
    assert len(found) >= 8  # the Users screen's seven and the audit view, at least
    return found


def fill(route: APIRoute, values: dict[str, str], override: tuple[str, str] | None = None) -> str:
    path = route.path
    for param in route.dependant.path_params:
        value = override[1] if override and override[0] == param.name else values.get(param.name)
        assert value is not None, f"{route.path}: add a valid value for {{{param.name}}}"
        path = path.replace("{" + param.name + "}", quote(value, safe=""))
    return path


def body_model(route: APIRoute) -> type[BaseModel] | None:
    params = route.dependant.body_params
    if not params:
        return None
    model = params[0].field_info.annotation
    assert isinstance(model, type), route.path
    assert issubclass(model, BaseModel), route.path
    return model


async def send(actor: Any, method: str, path: str, body: Any = None) -> Any:
    return await (actor.get(path) if method == "GET" else actor.call(method, path, body))


def rows(conn: Connection, actor_id: object) -> list[tuple[str, str, str | None]]:
    return [
        tuple(r)
        for r in conn.execute(
            text(
                "SELECT action, outcome, reason FROM admin_audit WHERE actor_id = :a "
                "ORDER BY created_at, id"
            ),
            {"a": actor_id},
        )
    ]


async def admin(env: Env, make_actor: Any, *, mfa: bool = True, stepup: bool = True) -> Any:
    user_id = await env.user(f"admin-{secrets.token_hex(4)}@example.com", role="admin")
    actor = await make_actor(user_id, mfa=mfa)
    if stepup:
        await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=user_id)
    actor.user_id = user_id
    return actor


async def target_with_entitlement(env: Env, conn: Connection) -> dict[str, str]:
    """A user with a live entitlement: the path values every route gets."""
    target = await env.user(f"rider-{secrets.token_hex(4)}@example.com")
    entitlement = conn.execute(
        text(
            "INSERT INTO entitlements (owner_id, plan_id, source) VALUES (:o, 'pro', 'grant') "
            "RETURNING id"
        ),
        {"o": target},
    ).scalar_one()
    return {"user_id": str(target), "entitlement_id": str(entitlement)}


# --- the gate on every route ------------------------------------------------------------------


async def test_every_admin_route_refuses_a_non_admin_and_audits_the_admin_refusals(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    values = {"user_id": str(uuid.uuid4()), "entitlement_id": str(uuid.uuid4())}
    plain = await env.user("plain@example.com")
    plain_actor = await make_actor(plain, mfa=True)
    await env.run("UPDATE sessions SET stepup_at = now() WHERE user_id = :u", u=plain)
    no_mfa = await admin(env, make_actor, mfa=False, stepup=False)
    no_stepup = await admin(env, make_actor, stepup=False)
    for method, route in routes(env):
        path, body = fill(route, values), BODIES.get((method, route.path))
        response = await send(plain_actor, method, path, body)
        assert (response.status_code, response.json()["detail"]["reason"]) == (403, "forbidden")
        before = len(rows(superuser, no_mfa.user_id))
        response = await send(no_mfa, method, path, body)
        assert (response.status_code, response.json()["detail"]["reason"]) == (
            403,
            "mfa_required",
        ), (method, route.path)
        assert rows(superuser, no_mfa.user_id)[before:] == [
            ("admin.request", "refused", "mfa_required")
        ]
        if method == "GET":
            continue
        before = len(rows(superuser, no_stepup.user_id))
        response = await send(no_stepup, method, path, body)
        assert (response.status_code, response.json()["detail"]["reason"]) == (
            403,
            "stepup_required",
        ), (method, route.path)
        assert rows(superuser, no_stepup.user_id)[before:] == [
            ("admin.request", "refused", "stepup_required")
        ]
    count = superuser.execute(
        text("SELECT count(*) FROM admin_audit WHERE actor_id = :a"), {"a": plain}
    ).scalar_one()
    assert count == 0  # a non-admin never gets a row (nor a maint connection)


# --- input fuzz across every route ------------------------------------------------------------


def fuzz_cases(
    route: APIRoute, method: str
) -> list[tuple[str, Callable[[str], tuple[tuple[str, str] | None, dict[str, Any] | None, Any]]]]:
    """``(field, case)`` pairs; ``case(bad)`` gives (path override, query, body)."""
    base = BODIES.get((method, route.path))
    model = body_model(route)
    assert model is None or base is not None, f"{method} {route.path}: add a valid body to BODIES"
    cases: list[Any] = []
    for param in route.dependant.path_params:
        cases.append((f"path:{param.name}", lambda bad, n=param.name: ((n, bad), None, base)))
    for param in route.dependant.query_params:
        cases.append((f"query:{param.alias}", lambda bad, n=param.alias: (None, {n: bad}, base)))
    for name, info in (model.model_fields if model else {}).items():
        is_list = get_origin(info.annotation) is list or any(
            get_origin(a) is list for a in get_args(info.annotation)
        )
        cases.append(
            (
                f"body:{name}",
                lambda bad, n=name, many=is_list: (
                    None,
                    None,
                    {**(base or {}), n: [bad] if many else bad},
                ),
            )
        )
    return cases


async def test_every_string_input_of_every_admin_route_is_refused_and_audited_once(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    actor = await admin(env, make_actor)
    values = await target_with_entitlement(env, superuser)
    tried: list[str] = []
    for method, route in routes(env):
        for field, case in fuzz_cases(route, method):
            for label, bad in BAD_VALUES.items():
                override, query, body = case(bad)
                path = fill(route, values, override)
                if query:
                    path += "?" + urlencode(query)
                before = len(rows(superuser, actor.user_id))
                response = await send(actor, method, path, body)
                where = f"{method} {route.path} {field} {label}"
                assert response.status_code in (404, 422), (where, response.text[:300])
                new = rows(superuser, actor.user_id)[before:]
                assert [row[1] for row in new] == ["refused"], (where, new)
                tried.append(where)
    assert any(
        t.startswith("POST /api/admin/users/{user_id}/entitlements body:note") for t in tried
    )
    assert any("/api/admin/audit query:actor_email" in t for t in tried)
    # Nothing the fuzz sent changed the target (read as the superuser, past RLS).
    kept = superuser.execute(
        text(
            "SELECT u.disabled_at IS NULL, (SELECT count(*) FROM entitlements e WHERE "
            "e.owner_id = u.id AND e.revoked_at IS NULL) FROM users u WHERE u.id = :u"
        ),
        {"u": values["user_id"]},
    ).one()
    assert tuple(kept) == (True, 1)


@pytest.mark.parametrize(
    ("path_suffix", "query", "reason"),
    [
        ("", {"limit": "0"}, "invalid_input"),  # FastAPI's own 422
        ("", {"since": "2026-10-02T00:00:00Z", "until": "2026-10-01T00:00:00Z"}, "period_invalid"),
    ],
)
async def test_an_input_refusal_after_the_gate_writes_one_refused_row(
    env: Env, make_actor: Any, superuser: Connection, path_suffix: str, query: dict[str, str],
    reason: str,
) -> None:  # fmt: skip
    """ADR 0011 section 10 (PR #281 review, Architect): an admin request that passes the gate
    and is refused for its input leaves a ``refused`` row, FastAPI's validation 422 and a route's
    own 422 raised before its session alike, with the route and method as params."""
    actor = await admin(env, make_actor)
    response = await actor.get(f"/api/admin/audit{path_suffix}?{urlencode(query)}")
    assert response.status_code == 422, response.text
    found = superuser.execute(
        text("SELECT action, outcome, reason, params FROM admin_audit WHERE actor_id = :a"),
        {"a": actor.user_id},
    ).all()
    assert [tuple(r) for r in found] == [
        ("admin.request", "refused", reason, {"method": "GET", "route": "/api/admin/audit"})
    ]


async def test_a_refusal_inside_a_session_is_not_audited_twice(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """``admin_session`` writes the row of its own ``AdminRefused``; the handler adds none."""
    actor = await admin(env, make_actor)
    response = await actor.get(f"/api/admin/users/{uuid.uuid4()}")
    assert response.status_code == 404
    assert rows(superuser, actor.user_id) == [("user.read", "refused", "not_found")]


async def test_a_nul_in_the_grant_note_is_422_not_a_500(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """PR #282 final review: ``entitlements.note`` refuses a NUL, which was a 500 with an
    ``error`` row. The note is now ``bounded_text``: 422, one ``refused`` row, nothing stored."""
    actor = await admin(env, make_actor)
    target = await env.user("t@example.com")
    for note in ("a\x00b", "line\nbreak", "\x7f"):
        response = await actor.post(
            f"/api/admin/users/{target}/entitlements", {"plan_id": "pro", "note": note}
        )
        assert response.status_code == 422, response.text
    assert rows(superuser, actor.user_id) == [("admin.request", "refused", "invalid_input")] * 3
    stored = superuser.execute(
        text("SELECT count(*) FROM entitlements WHERE owner_id = :o"), {"o": target}
    ).scalar_one()
    assert stored == 0
    fine = await actor.post(
        f"/api/admin/users/{target}/entitlements", {"plan_id": "pro", "note": "é" * 200}
    )
    assert fine.status_code == 201, fine.text


# --- planted secrets never leave through an admin route ---------------------------------------


def plant_secrets(conn: Connection, owner: uuid.UUID) -> list[str]:
    """One row of every table with a secret column (``maint_guard.SECRET_COLUMNS``) that a
    user's account can hold, each secret column a distinct planted value; the needles to look
    for (texts as they are, bytes as hex, base64 and latin-1 text)."""
    import base64

    raw = {k: secrets.token_bytes(n) for k, n in (
        ("c_secret", 40), ("c_nonce", 12), ("c_wrapped", 48), ("c_dek_nonce", 12),
        ("t_secret", 32), ("t_nonce", 12), ("t_wrapped", 48), ("t_dek_nonce", 12),
        ("recovery", 32), ("rev_hash", 32), ("op_hash", 32), ("pool_hash", 32), ("flow", 32),
        ("state", 32), ("sp_state", 32), ("sp_session", 32), ("device", 32), ("token", 32),
        ("job_hash", 32), ("sw_content", 32), ("sw_state", 32),
    )}  # fmt: skip
    tag = secrets.token_hex(5)
    words = {
        "password": f"$argon2id$v=19$PLANTEDpw{tag}",
        "c_key": f"kek-plantedc{tag}",
        "t_key": f"kek-plantedt{tag}",
        "client_id": secrets.token_hex(16),
        "title": f"PLANTED title {tag}",
        "brief": f"PLANTED brief {tag}",
        "content": f"PLANTED revision {tag}",
        "music": f"PLANTED music {tag}",
        "provenance": f"PLANTED provenance {tag}",
        "message": f"PLANTED message {tag}",
        "change": f"PLANTED change {tag}",
        "op_key": f"PLANTEDopkey{tag}",
        "external": f"PLANTEDexternal{tag}",
        "result": f"PLANTED result {tag}",
        "context": f"PLANTED context {tag}",
        "spotify_user": f"planted-spotify-{tag}",
        "pool_playlist": f"Pool{tag}PLANTED{secrets.token_hex(4)}"[:22].ljust(22, "x"),
        "subject": f"planted-subject-{tag}",
        "email_at_link": f"planted-link-{tag}@example.org",
        "oauth_nonce": f"PLANTEDnonce{tag}",
        "pkce": f"PLANTEDpkce{tag}",
        "return_to": f"/planted/return/{tag}",
        "sp_client": secrets.token_hex(16),
        "redirect": f"http://127.0.0.1/planted/{tag}",
        "verifier": (f"PLANTEDverifier{tag}" + "v" * 43)[:43],
        "auth_ip": f"203.0.113.{secrets.randbelow(200) + 1}-{tag}",
        "auth_detail": f"PLANTED detail {tag}",
        "session_ip": f"198.51.100.{secrets.randbelow(200) + 1}",
        "user_agent": f"PLANTED-agent/{tag}",
        "job_input": f"PLANTED job input {tag}",
        "job_key": f"PLANTEDjobkey{tag}",
        "event": f"PLANTED event {tag}",
        "sw_name": f"PLANTED workout {tag}",
        "sw_program": str(900_000_000_000 + secrets.randbelow(10**9)),
        "link_source": f"Link{tag}PLANTED{secrets.token_hex(4)}"[:22].ljust(22, "y"),
        "link_playlist": str(800_000_000_000 + secrets.randbelow(10**9)),
    }
    class_id = uuid.uuid4()

    def run(sql: str, **params: Any) -> Any:
        return conn.execute(text(sql), {"o": owner, "c": class_id, **params})

    def js(value: str) -> str:
        return json.dumps({"x": value})

    run("UPDATE users SET password_hash = :p WHERE id = :o", p=words["password"])
    run("UPDATE sessions SET ip = :i, user_agent = :a WHERE user_id = :o",
        i=words["session_ip"], a=words["user_agent"])  # fmt: skip
    run("INSERT INTO connections (owner_id, provider, status, public, secret, secret_nonce, "
        "wrapped_dek, dek_nonce, key_id) VALUES (:o, 'spotify', 'connected', CAST(:p AS jsonb), "
        ":s, :n, :w, :d, :k)", p=json.dumps({"client_id": words["client_id"]}),
        s=raw["c_secret"], n=raw["c_nonce"], w=raw["c_wrapped"], d=raw["c_dek_nonce"],
        k=words["c_key"])  # fmt: skip
    run("INSERT INTO mfa_totp (user_id, secret_ciphertext, nonce, key_id, wrapped_dek, dek_nonce) "
        "VALUES (:o, :s, :n, :k, :w, :d)", s=raw["t_secret"], n=raw["t_nonce"], k=words["t_key"],
        w=raw["t_wrapped"], d=raw["t_dek_nonce"])  # fmt: skip
    run("INSERT INTO mfa_recovery_codes (user_id, code_hash) VALUES (:o, :h)", h=raw["recovery"])
    run("INSERT INTO classes (id, owner_id, title, brief) VALUES (:c, :o, :t, CAST(:b AS jsonb))",
        t=words["title"], b=js(words["brief"]))  # fmt: skip
    run("INSERT INTO class_revisions (owner_id, class_id, number, kind, content, content_hash, "
        "music, provenance) VALUES (:o, :c, 1, 'draft', CAST(:a AS jsonb), :h, CAST(:m AS jsonb), "
        "CAST(:p AS jsonb))", a=js(words["content"]), h=raw["rev_hash"], m=js(words["music"]),
        p=js(words["provenance"]))  # fmt: skip
    run("INSERT INTO class_messages (owner_id, class_id, seq, role, body, change) "
        "VALUES (:o, :c, 1, 'user', :b, CAST(:x AS jsonb))", b=words["message"],
        x=js(words["change"]))  # fmt: skip
    job = run("INSERT INTO jobs (owner_id, kind, state, class_id, input, idempotency_key, "
              "request_hash, credit_state) VALUES (:o, 'test_kind', 'queued', :c, "
              "CAST(:i AS jsonb), :k, :h, 'none') RETURNING id", i=js(words["job_input"]),
              k=words["job_key"], h=raw["job_hash"]).scalar_one()  # fmt: skip
    run(
        "INSERT INTO job_events (owner_id, job_id, seq, kind, payload, lease_version) "
        "VALUES (:o, :j, 1, 'queued', CAST(:p AS jsonb), 0)",
        j=job,
        p=js(words["event"]),
    )
    run("INSERT INTO external_operations (owner_id, kind, idempotency_key, request_hash, state, "
        "external_id, result, request_context, lease_version) VALUES (:o, "
        "'spotify_create_playlist', :k, :h, 'succeeded', :e, CAST(:r AS jsonb), "
        "CAST(:x AS jsonb), 0)", k=words["op_key"], h=raw["op_hash"], e=words["external"],
        r=js(words["result"]), x=js(words["context"]))  # fmt: skip
    run("INSERT INTO spotify_pool (owner_id, spotify_user_id, playlist_id, consent_version, "
        "last_hash) VALUES (:o, :u, :p, 1, :h)", u=words["spotify_user"],
        p=words["pool_playlist"], h=raw["pool_hash"])  # fmt: skip
    run(
        "INSERT INTO identities (user_id, provider, subject, email_at_link) "
        "VALUES (:o, 'google', :s, :e)",
        s=words["subject"],
        e=words["email_at_link"],
    )
    run("INSERT INTO oauth_flows (flow_hash, provider, purpose, state_hash, nonce, pkce_verifier, "
        "user_id, return_to, expires_at) VALUES (:f, 'google', 'link', :s, :n, :p, :o, :r, "
        "now() + interval '5 minutes')", f=raw["flow"], s=raw["state"], n=words["oauth_nonce"],
        p=words["pkce"], r=words["return_to"])  # fmt: skip
    run("INSERT INTO spotify_oauth_flows (owner_id, state_hash, session_hash, client_id, "
        "redirect_uri, verifier, expires_at) VALUES (:o, :s, :h, :i, :r, :v, "
        "now() + interval '5 minutes')", s=raw["sp_state"], h=raw["sp_session"],
        i=words["sp_client"], r=words["redirect"], v=words["verifier"])  # fmt: skip
    run("INSERT INTO known_devices (user_id, device_hash) VALUES (:o, :h)", h=raw["device"])
    run(
        "INSERT INTO auth_tokens (purpose, token_hash, user_id, expires_at) VALUES "
        "('reset_password', :h, :o, now() + interval '1 hour')",
        h=raw["token"],
    )
    run(
        "INSERT INTO auth_events (kind, user_id, ip, detail) VALUES ('sign_in', :o, :i, "
        "CAST(:d AS jsonb))",
        i=words["auth_ip"],
        d=js(words["auth_detail"]),
    )
    run("INSERT INTO saved_workouts (owner_id, class_id, program_id, revision_number, "
        "content_hash, state_hash, name) VALUES (:o, :c, :p, 1, :h, :s, :n)",
        p=int(words["sw_program"]), h=raw["sw_content"], s=raw["sw_state"],
        n=words["sw_name"])  # fmt: skip
    run("INSERT INTO mowl_playlist_links (owner_id, source, source_id, playlist_id) "
        "VALUES (:o, 'spotify', :s, :p)", s=words["link_source"],
        p=int(words["link_playlist"]))  # fmt: skip
    needles = list(words.values())
    for value in raw.values():
        needles += [value.hex(), base64.b64encode(value).decode(), value.decode("latin-1")]
    return needles


async def test_no_admin_route_returns_a_planted_secret_of_any_table(
    env: Env, make_actor: Any, superuser: Connection
) -> None:
    """PR #277 review: every secret-bearing column (non-zero nonces and wrapped keys, key ids,
    TOTP and recovery codes, revisions, messages, external operations, the Spotify pool,
    identities, both OAuth flow tables, devices, tokens, events, saved workouts and links) is
    planted for one user; no response of any admin route contains one, the audit view
    included (which shows every call's params)."""
    actor = await admin(env, make_actor)
    values = await target_with_entitlement(env, superuser)
    owner = uuid.UUID(values["user_id"])
    rider = await make_actor(owner)  # a session row to plant ip and user agent into
    needles = [*plant_secrets(superuser, owner), rider.token, actor.token]
    responses = []
    for method, route in routes(env):
        path = fill(route, values)
        if route.path.endswith("/users"):
            path += "?q=rider"
        responses.append(await send(actor, method, path, BODIES.get((method, route.path))))
    responses.append(await actor.get("/api/admin/audit?limit=100"))
    assert all(r.status_code < 500 for r in responses), [r.status_code for r in responses]
    detail = [r for r in responses if r.request.method == "GET"
              and r.request.url.path.endswith(values["user_id"])]  # fmt: skip
    assert [r.status_code for r in detail] == [200]  # the detail answered, connections and all
    for response in responses:
        leaked = [n for n in needles if len(n) >= 8 and n in response.text]
        assert leaked == [], (response.request.method, response.request.url.path)
