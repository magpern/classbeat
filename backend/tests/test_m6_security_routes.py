"""M6 security suite, the route-table matrices (roadmap M6, issue #339; ADR 0002, ADR 0003 section
7, ADR 0011 section 6). Fakes only, no network.

The app's route table is the input, not a list a person keeps in step with it:

- ``CLASSIFIED`` says what every ``(method, path)`` of the app is. A route in the table but not in
  ``CLASSIFIED``, and an entry of ``CLASSIFIED`` no longer in the table, both fail
  ``test_every_route_is_classified``: a new route cannot ship without a decision about who may
  call it and what another user's id must do.
- ``admin``: every ``/api/admin`` route answers 403 to a user who is not an admin (``forbidden``),
  and to an admin without a current TOTP check (none at all, or one older than 12 hours:
  ``mfa_required``), before its path or body is read (junk ids and an empty body still give 403)
  and without changing a row or leaving an ``ok`` audit row.
- ``owned``: every route whose path carries the id of an owned row answers 404 for another
  user's id, with the very body an unknown id gets (no oracle for existence), changes nothing,
  and the owner's own call to the same route is not a 404 (so the ids are real). A route with two
  ids (class and revision) is crossed both ways.
- ``owned_body``: the job create names the owner's rows in its body; another user's ids are
  refused the same way.
- ``session``, ``owned`` and ``admin`` routes refuse a caller without a session (401).
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from authtools import ORIGIN, Env, running_env
from classbeat.admin.routes import _calls, admin_gate  # pyright: ignore[reportPrivateUsage]
from classbeat.auth import deps as auth_deps
from classbeat.config import Settings
from classbeat.web import create_app
from jobapi import UNKNOWN
from jobcore import _INSERT_REVISION  # pyright: ignore[reportPrivateUsage]
from jobtools import new_class, new_job
from mfatools import Actor
from savetools import DRAFT, draft_hash

# --- the classification -------------------------------------------------------------------------

# public: no session needed (sign-in, sign-up, reset, OAuth redirects, health).
# session: needs a session and works on the caller's own data; no foreign id in the path.
# owned: the path carries the id of an owned row (404 for another user's).
# owned_body: the body (or a query) carries ids of owned rows.
# admin: under /api/admin (role admin, TOTP within 12 hours, step-up for writes).
# dev_only: API docs, off when ``environment`` is ``prod``.
CLASSIFIED: dict[tuple[str, str], str] = {
    ("GET", "/api/openapi.json"): "dev_only",
    ("GET", "/api/docs"): "dev_only",
    ("GET", "/docs/oauth2-redirect"): "dev_only",
    ("GET", "/api/health"): "public",
    ("GET", "/api/ready"): "public",
    ("GET", "/api/settings/public"): "public",
    ("GET", "/oauth/spotify/callback"): "public",
    ("GET", "/oauth/{provider}/callback"): "public",
    ("GET", "/api/auth/session"): "public",
    ("GET", "/api/auth/oauth/providers"): "public",
    ("GET", "/api/auth/oauth/pending"): "public",
    ("POST", "/api/auth/oauth/pending/email"): "public",
    ("POST", "/api/auth/oauth/{provider}/start"): "public",
    ("POST", "/api/auth/signup"): "public",
    ("POST", "/api/auth/signup/confirm"): "public",
    ("POST", "/api/auth/login"): "public",
    ("POST", "/api/auth/password/reset/request"): "public",
    ("POST", "/api/auth/password/reset/info"): "public",
    ("POST", "/api/auth/password/reset/complete"): "public",
    ("POST", "/api/auth/email/confirm"): "session",
    ("POST", "/api/auth/mfa/verify"): "session",
    ("GET", "/api/auth/sessions"): "session",
    ("DELETE", "/api/auth/sessions/{session_id}"): "owned",
    ("POST", "/api/auth/logout"): "public",
    ("POST", "/api/auth/logout-all"): "session",
    ("POST", "/api/auth/reauth"): "session",
    ("POST", "/api/auth/reauth/totp"): "session",
    ("POST", "/api/auth/password/change"): "session",
    ("POST", "/api/auth/email/change"): "session",
    ("GET", "/api/account/export"): "session",
    ("POST", "/api/account/delete"): "session",
    ("GET", "/api/auth/mfa"): "session",
    ("POST", "/api/auth/mfa/totp/start"): "session",
    ("POST", "/api/auth/mfa/totp/confirm"): "session",
    ("DELETE", "/api/auth/mfa/totp"): "session",
    ("POST", "/api/auth/mfa/recovery-codes"): "session",
    ("POST", "/api/auth/mfa/stepup"): "session",
    ("GET", "/api/auth/identities"): "session",
    ("DELETE", "/api/auth/identities/{identity_id}"): "owned",
    ("GET", "/api/connections"): "session",
    ("POST", "/api/connections/mowl"): "session",
    ("DELETE", "/api/connections/mowl"): "session",
    ("POST", "/api/connections/swedebeat"): "session",
    ("DELETE", "/api/connections/swedebeat"): "session",
    ("GET", "/api/connections/spotify"): "session",
    ("POST", "/api/connections/spotify/start"): "session",
    ("DELETE", "/api/connections/spotify"): "session",
    ("PUT", "/api/connections/spotify/pool-consent"): "session",
    ("DELETE", "/api/connections/spotify/pool-consent"): "session",
    ("POST", "/api/jobs"): "owned_body",
    ("GET", "/api/jobs/{job_id}"): "owned",
    ("GET", "/api/jobs/{job_id}/events"): "owned",
    ("GET", "/api/jobs/{job_id}/revision"): "owned",
    ("POST", "/api/jobs/{job_id}/cancel"): "owned",
    ("POST", "/api/jobs/{job_id}/approve"): "owned",
    ("GET", "/api/usage"): "session",
    ("GET", "/api/account/plan"): "session",
    ("POST", "/api/account/redeem"): "session",
    ("POST", "/api/account/trial"): "session",
    ("GET", "/api/external-operations"): "owned_body",
    ("GET", "/api/external-operations/{operation_id}"): "owned",
    ("POST", "/api/external-operations/{operation_id}/check-again"): "owned",
    ("POST", "/api/external-operations/{operation_id}/do-it-anyway"): "owned",
    ("GET", "/api/music/mowl-playlists"): "session",
    ("GET", "/api/music/class-playlists"): "session",
    ("GET", "/api/classes"): "session",
    ("GET", "/api/classes/{class_id}"): "owned",
    ("GET", "/api/classes/{class_id}/revisions"): "owned",
    ("GET", "/api/classes/{class_id}/revisions/{revision_id}"): "owned",
    ("GET", "/api/classes/{class_id}/messages"): "owned",
    ("POST", "/api/classes/{class_id}/current"): "owned",
    ("DELETE", "/api/classes/{class_id}"): "owned",
    ("GET", "/api/admin/whoami"): "admin",
    ("GET", "/api/admin/users"): "admin",
    ("GET", "/api/admin/users/{user_id}"): "admin",
    ("POST", "/api/admin/users/{user_id}/entitlements"): "admin",
    ("POST", "/api/admin/users/{user_id}/entitlements/{entitlement_id}/revoke"): "admin",
    ("POST", "/api/admin/users/{user_id}/disable"): "admin",
    ("POST", "/api/admin/users/{user_id}/enable"): "admin",
    ("DELETE", "/api/admin/users/{user_id}"): "admin",
    ("GET", "/api/admin/audit"): "admin",
    ("GET", "/api/admin/usage"): "admin",
    ("POST", "/api/admin/users/{user_id}/credits/{ledger_id}/refund"): "admin",
    ("GET", "/api/admin/jobs"): "admin",
    ("GET", "/api/admin/invites"): "admin",
    ("POST", "/api/admin/invites"): "admin",
    ("POST", "/api/admin/invites/{invite_id}/disable"): "admin",
    ("GET", "/api/admin/redeem-codes"): "admin",
    ("POST", "/api/admin/redeem-codes"): "admin",
    ("POST", "/api/admin/redeem-codes/{code_id}/disable"): "admin",
    ("GET", "/api/admin/settings"): "admin",
    ("PUT", "/api/admin/settings/{key}"): "admin",
    ("DELETE", "/api/admin/settings/{key}"): "admin",
    ("PATCH", "/api/admin/plans/{plan_id}"): "admin",
    ("GET", "/api/admin/models"): "admin",
    ("PUT", "/api/admin/models/routes/{route_key}/{stage_group}"): "admin",
    ("DELETE", "/api/admin/models/routes/{route_key}/{stage_group}"): "admin",
}
CATEGORIES = {"public", "session", "owned", "owned_body", "admin", "dev_only"}
IGNORED_METHODS = {"HEAD", "OPTIONS"}


def table_of(app: Any) -> set[tuple[str, str]]:
    """Every ``(method, full path)`` the app serves, through included routers."""
    found: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        path = context.path or getattr(route, "path", "") or ""
        methods: set[str] = set(getattr(route, "methods", None) or set())
        assert methods, f"{path}: not a method route ({type(route).__name__}); classify it here"
        found |= {(m, path) for m in methods - IGNORED_METHODS}
    return found


def in_category(category: str) -> list[tuple[str, str]]:
    return sorted(key for key, found in CLASSIFIED.items() if found == category)


def test_every_route_is_classified(tmp_path: Any) -> None:
    """The route table of a full app (admin area and song picking on) equals ``CLASSIFIED``: a new
    route fails here until it is classified, and with it the matrices below cover it."""
    app = create_app(_settings())
    actual = table_of(app)
    unclassified = sorted(actual - CLASSIFIED.keys())
    stale = sorted(CLASSIFIED.keys() - actual)
    assert unclassified == [], (
        f"new routes without a classification in test_m6_security_routes.CLASSIFIED: {unclassified}"
    )
    assert stale == [], f"classified routes the app no longer serves: {stale}"
    assert set(CLASSIFIED.values()) <= CATEGORIES
    assert all(isinstance(r, APIRoute) for r in _api_routes(app) if r.path.startswith("/api/admin"))


def _api_routes(app: Any) -> list[Any]:
    return [c.original_route for c in iter_route_contexts(app.routes)]


# The dependencies that make a route need a signed-in caller (``classbeat.auth.deps``) or an admin.
AUTH_DEPENDENCIES = {
    auth_deps.require_user,
    auth_deps.require_recent_auth,
    auth_deps.require_admin,
    auth_deps.require_stepup,
    admin_gate,
}
# ``public`` routes that read the session if there is one (``current_session``) without needing
# it: the session probe, sign-out, and the OAuth start and callbacks (which link to the caller's
# account when there is one). Any other public route may not touch the session at all.
OPTIONAL_SESSION = {
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/oauth/{provider}/start"),
    ("GET", "/oauth/{provider}/callback"),
    # A browser returning from Spotify: without a session it connects nothing and redirects.
    ("GET", "/oauth/spotify/callback"),
}


def dependencies_of(app: Any) -> dict[tuple[str, str], set[Any]]:
    found: dict[tuple[str, str], set[Any]] = {}
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            path = context.path or route.path
            for method in (route.methods or set()) - IGNORED_METHODS:
                found[(method, path)] = _calls(route.dependant)
    return found


def test_the_classification_agrees_with_what_each_route_depends_on() -> None:
    """Read from the route's own dependencies, not from its name: a route that needs a signed-in
    user (or an admin) is never ``public``, every ``session``, ``owned``, ``owned_body`` and
    ``admin`` route has such a dependency, and a ``public`` route may read the session only if it
    is one of the four that do so on purpose."""
    problems: list[str] = []
    dependencies = dependencies_of(create_app(_settings()))
    seen_public = 0
    for key, category in CLASSIFIED.items():
        if category == "dev_only":
            continue
        calls = dependencies[key]
        guarded = bool(calls & AUTH_DEPENDENCIES)
        if category == "public":
            seen_public += 1
            if guarded:
                problems.append(f"{key} is public but depends on an auth requirement")
            if auth_deps.current_session in calls and key not in OPTIONAL_SESSION:
                problems.append(f"{key} is public and reads the session, but is not listed")
        elif not guarded:
            problems.append(f"{key} is {category} but has no auth dependency")
    assert seen_public >= 14  # not vacuous
    assert {k for k, v in CLASSIFIED.items() if v == "public"} >= OPTIONAL_SESSION
    assert problems == []


async def test_every_public_route_answers_an_anonymous_caller(env: Env) -> None:
    """Each ``public`` route is reached without a session (not 401, no server error) with an
    empty body, so none is public on paper only; the refusals are the route's own (400, 404, 422,
    a closed sign-up), never a demand for a session."""
    from flows import Browser

    browser = Browser(env)
    routes = in_category("public")
    assert len(routes) >= 14
    problems: list[str] = []
    for method, template in routes:
        path = fill(template, provider="google", session_id=UNKNOWN)
        if method == "GET":
            response = await browser.get(path)
        else:
            response = await browser.request(method, path, {})
        if response.status_code == 401 or response.status_code >= 500:
            problems.append(f"{method} {template}: {response.status_code} {response.text[:100]}")
    assert problems == []


def test_every_api_admin_route_is_classified_admin_and_the_reverse() -> None:
    """The prefix decides: nothing under /api/admin may be classified as anything else, and no
    other path may be called admin."""
    for (method, path), category in CLASSIFIED.items():
        assert (category == "admin") == path.startswith("/api/admin"), (method, path, category)


def test_a_production_app_serves_no_api_docs() -> None:
    app = create_app(_settings(environment="prod"))
    served = table_of(app)
    docs = {key for key in served if CLASSIFIED.get(key) == "dev_only"}
    assert docs == set()
    assert served <= CLASSIFIED.keys()


def _settings(**overrides: object) -> Settings:
    from authtools import make_settings

    values: dict[str, object] = {
        "song_picking": True,
        "maint_database_url": "postgresql+psycopg://classbeat_maint@127.0.0.1:1/classbeat",
        "database_maint_password": "unused-in-this-test",
        **overrides,
    }
    return make_settings("postgresql+psycopg://classbeat_app:x@127.0.0.1:1/classbeat", **values)


# --- the running app ----------------------------------------------------------------------------


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    """The app with the admin area (maint credentials), so an admin route that passed the gate
    would really run; ``make_actor`` signs in to it."""
    url = make_url(maint_database_url)
    async with running_env(
        app_database_url,
        maint_database_url=url.set(password=None).render_as_string(hide_password=False),
        database_maint_password=url.password,
        song_picking=True,
        jobs_per_user=16,
    ) as running:
        yield running


def fill(template: str, **values: object) -> str:
    for name, value in values.items():
        template = template.replace("{" + name + "}", str(value))
    return template


async def call(actor: Actor | None, env: Env, method: str, path: str, body: Any = None) -> Any:
    """``actor``'s request (CSRF and Origin as a browser sends them), or an anonymous one."""
    if actor is not None:
        return await actor.call(method, path, body)
    headers = {"Origin": ORIGIN}
    response = await env.client.request(method, path, headers=headers, json=body)
    env.client.cookies.clear()
    return response


def reason_of(response: Any) -> str | None:
    try:
        return response.json()["detail"]["reason"]
    except (KeyError, TypeError, ValueError):
        return None


# --- admin routes -------------------------------------------------------------------------------

ADMIN_IDS = {
    "user_id": "11111111-1111-4111-8111-111111111111",
    "entitlement_id": "22222222-2222-4222-8222-222222222222",
    "ledger_id": "33333333-3333-4333-8333-333333333333",
    "invite_id": "44444444-4444-4444-8444-444444444444",
    "code_id": "55555555-5555-4555-8555-555555555555",
    "key": "trial",
    "plan_id": "free",
    "route_key": "openai",
    "stage_group": "design",
}
JUNK_IDS = dict.fromkeys(ADMIN_IDS, "not-an-id-' OR 1=1 --")
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
SNAPSHOT_TABLES = (
    "users",
    "entitlements",
    "code_redemptions",
    "credit_ledger",
    "invites",
    "redeem_codes",
    "app_settings",
    "plans",
    "model_routes",
    "classes",
    "jobs",
)


def snapshot(conn: Connection) -> dict[str, str]:
    """A digest per table of everything a refused admin call must not change."""
    out: dict[str, str] = {}
    for table in SNAPSHOT_TABLES:
        out[table] = conn.execute(
            text(
                "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) "  # noqa: S608
                f"FROM {table} t"
            )
        ).scalar_one()
    return out


@dataclass
class Callers:
    plain: Actor  # a user with a fresh TOTP check: the role is what is missing
    plain_no_mfa: Actor
    admin_no_totp: Actor  # role admin, never checked a TOTP code
    admin_stale: Actor  # role admin, last TOTP check 13 hours ago
    admin_no_stepup: Actor  # role admin, fresh TOTP check, no step-up (for the writes)
    ids: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())


@pytest.fixture
async def callers(env: Env, make_actor: Any, superuser: Connection) -> Callers:
    plain = await make_actor(await env.user("plain@example.com"), mfa=True)
    plain_no_mfa = await make_actor(await env.user("plain2@example.com"))
    admin_no_totp = await make_actor(await env.user("a1@example.com", role="admin"))
    stale_id = await env.user("a2@example.com", role="admin")
    admin_stale = await make_actor(stale_id, mfa=True)
    superuser.execute(
        text("UPDATE sessions SET mfa_at = now() - interval '13 hours' WHERE user_id = :u"),
        {"u": stale_id},
    )
    admin_no_stepup = await make_actor(await env.user("a3@example.com", role="admin"), mfa=True)
    return Callers(plain, plain_no_mfa, admin_no_totp, admin_stale, admin_no_stepup)


@pytest.mark.parametrize("ids", [ADMIN_IDS, JUNK_IDS], ids=["plausible_ids", "junk_ids"])
async def test_admin_routes_are_403_for_non_admins_and_admins_without_a_current_totp(
    env: Env, callers: Callers, superuser: Connection, ids: dict[str, str]
) -> None:
    routes = in_category("admin")
    assert len(routes) >= 25  # not vacuous
    before = snapshot(superuser)
    expected = [
        (callers.plain, "forbidden"),
        (callers.plain_no_mfa, "forbidden"),
        (callers.admin_no_totp, "mfa_required"),
        (callers.admin_stale, "mfa_required"),
    ]
    problems: list[str] = []
    for method, template in routes:
        path = fill(template, **ids)
        # An empty object where the route reads a body: a gate that ran after the validation
        # would answer 422 here.
        body = {} if method in WRITES else None
        for actor, reason in expected:
            response = await actor.call(method, path, body)
            if (response.status_code, reason_of(response)) != (403, reason):
                problems.append(
                    f"{method} {template}: {response.status_code} {response.text[:120]} "
                    f"(wanted 403 {reason})"
                )
            elif response.headers.get("cache-control") != "no-store":
                problems.append(f"{method} {template}: no Cache-Control: no-store on the refusal")
    assert problems == []
    assert snapshot(superuser) == before  # nothing was changed
    outcomes = {r.outcome for r in superuser.execute(text("SELECT outcome FROM admin_audit"))}
    assert "ok" not in outcomes  # a refused call leaves no ok row
    plain_rows = superuser.execute(
        text("SELECT count(*) FROM admin_audit a JOIN users u ON u.id = a.actor_id "
             "WHERE u.role <> 'admin'")
    ).scalar_one()  # fmt: skip
    assert plain_rows == 0  # and a non-admin none at all


async def test_every_admin_write_also_needs_a_recent_stepup(
    env: Env, callers: Callers, superuser: Connection
) -> None:
    """An admin with a current TOTP check but no step-up is refused every write (403
    ``stepup_required``) and may read: the reads pass the gate (not 403) and fail only on what
    they need, or answer."""
    writes = [r for r in in_category("admin") if r[0] in WRITES]
    reads = [r for r in in_category("admin") if r[0] not in WRITES]
    assert len(writes) >= 12
    before = snapshot(superuser)
    for method, template in writes:
        response = await callers.admin_no_stepup.call(method, fill(template, **ADMIN_IDS), {})
        assert (response.status_code, reason_of(response)) == (403, "stepup_required"), (
            method,
            template,
            response.text,
        )
    assert snapshot(superuser) == before
    for method, template in reads:
        response = await callers.admin_no_stepup.get(fill(template, **ADMIN_IDS))
        assert response.status_code != 403, (method, template, response.text)


@pytest.mark.parametrize("category", ["admin", "session", "owned"])
async def test_routes_that_need_a_session_refuse_an_anonymous_caller(
    env: Env, category: str
) -> None:
    problems: list[str] = []
    routes = in_category(category)
    assert routes
    for method, template in routes:
        path = fill(template, **ADMIN_IDS | {k: UNKNOWN for k in OWNED_PATH_PARAMS})
        response = await call(None, env, method, path, {} if method in WRITES else None)
        # A write without the CSRF token of a session is refused by the middleware first.
        refused = response.status_code == 401 or (
            method in WRITES
            and response.status_code == 403
            and reason_of(response) == "csrf_token_missing"
        )
        if not refused:
            problems.append(f"{method} {template}: {response.status_code} {response.text[:100]}")
    assert problems == []


# --- owned routes -------------------------------------------------------------------------------

OWNED_PATH_PARAMS = (
    "session_id",
    "identity_id",
    "job_id",
    "operation_id",
    "class_id",
    "revision_id",
)


@dataclass
class Rows:
    """One user's rows, one of each owned kind, and the session that holds them."""

    user: uuid.UUID
    class_id: uuid.UUID
    job_id: uuid.UUID
    revision_id: uuid.UUID
    operation_id: uuid.UUID
    session_id: uuid.UUID
    identity_id: uuid.UUID

    @property
    def digest(self) -> str:
        return draft_hash(DRAFT).hex()

    def params(self) -> dict[str, uuid.UUID]:
        return {
            "class_id": self.class_id,
            "job_id": self.job_id,
            "revision_id": self.revision_id,
            "operation_id": self.operation_id,
            "session_id": self.session_id,
            "identity_id": self.identity_id,
        }


async def plant(env: Env, conn: Connection, user: uuid.UUID) -> Rows:
    """A user with one class, a job and a draft revision of it, an uncertain Spotify operation, a
    second session and a linked sign-in identity. Written as the superuser (test setup)."""
    class_id = new_class(conn, user)
    job_id = new_job(
        conn, user, class_id, state="completed", credit_state="committed", finished_at=_now(conn)
    )
    revision_id = uuid.uuid4()
    conn.execute(
        _INSERT_REVISION,
        {
            "id": revision_id,
            "kind": "draft",
            "content": json.dumps(DRAFT),
            "hash": draft_hash(DRAFT),
            "job_id": job_id,
            "owner_id": user,
        },
    )
    operation_id = conn.execute(
        text(
            "INSERT INTO external_operations (owner_id, job_id, class_id, kind, idempotency_key, "
            "request_hash, state, lease_version) VALUES (:o, :j, :c, 'spotify_create_playlist', "
            ":k, :h, 'uncertain', 1) RETURNING id"
        ),
        {"o": user, "j": job_id, "c": class_id, "k": uuid.uuid4().hex, "h": b"\x01" * 32},
    ).scalar_one()
    identity_id = conn.execute(
        text(
            "INSERT INTO identities (user_id, provider, subject) VALUES (:u, 'github', :s) "
            "RETURNING id"
        ),
        {"u": user, "s": uuid.uuid4().hex},
    ).scalar_one()
    other_session = (await env.login(user)).id
    return Rows(user, class_id, job_id, revision_id, operation_id, other_session, identity_id)


def _now(conn: Connection) -> Any:
    return conn.execute(text("SELECT now()")).scalar_one()


@dataclass(frozen=True)
class OwnedRoute:
    method: str
    template: str
    body: Callable[[Rows], Any] | None = None
    # What the owner's own call may answer: never 404. ``None``: no owner call (it would reach a
    # provider or is the destructive twin of a read checked on the same ids).
    control: frozenset[int] | None = frozenset({200})

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.template)


def _pick(rows: Rows) -> dict[str, str]:
    return {"revision_id": str(rows.revision_id), "content_hash": rows.digest}


OWNED_ROUTES = [
    OwnedRoute("GET", "/api/jobs/{job_id}"),
    OwnedRoute("GET", "/api/jobs/{job_id}/revision"),
    OwnedRoute("GET", "/api/jobs/{job_id}/events?after=1000000", control=frozenset({204})),
    OwnedRoute("POST", "/api/jobs/{job_id}/cancel", control=frozenset({200, 409})),
    OwnedRoute("POST", "/api/jobs/{job_id}/approve", _pick, frozenset({409})),
    OwnedRoute("GET", "/api/external-operations/{operation_id}"),
    OwnedRoute("POST", "/api/external-operations/{operation_id}/check-again", control=None),
    OwnedRoute("POST", "/api/external-operations/{operation_id}/do-it-anyway", control=None),
    OwnedRoute("GET", "/api/classes/{class_id}"),
    OwnedRoute("GET", "/api/classes/{class_id}/revisions"),
    OwnedRoute("GET", "/api/classes/{class_id}/revisions/{revision_id}"),
    OwnedRoute("GET", "/api/classes/{class_id}/messages"),
    OwnedRoute("POST", "/api/classes/{class_id}/current", _pick, frozenset({200, 409})),
    OwnedRoute("DELETE", "/api/classes/{class_id}", control=frozenset({200, 409})),
    OwnedRoute("DELETE", "/api/auth/sessions/{session_id}", control=frozenset({204})),
    OwnedRoute("DELETE", "/api/auth/identities/{identity_id}", control=frozenset({204, 409})),
]


def test_every_owned_route_has_a_case() -> None:
    """Each ``owned`` route of the classification has a case below, and the reverse."""
    cases = {(r.method, r.template.split("?")[0]) for r in OWNED_ROUTES}
    assert cases == set(in_category("owned"))


def owned_state(conn: Connection, rows: Rows) -> dict[str, Any]:
    """What a foreign call must leave as it was: the owner's rows, their states, the session."""

    def one(sql: str) -> Any:
        return conn.execute(text(sql), {"u": rows.user, "j": rows.job_id}).all()

    return {
        "classes": one("SELECT c::text FROM classes c WHERE owner_id = :u"),
        "jobs": one("SELECT id, state, credit_state FROM jobs WHERE owner_id = :u"),
        "revisions": one("SELECT id FROM class_revisions WHERE owner_id = :u"),
        "operations": one("SELECT id, state FROM external_operations WHERE owner_id = :u"),
        "sessions": one("SELECT count(*) FROM sessions WHERE user_id = :u"),
        "identities": one("SELECT id FROM identities WHERE user_id = :u"),
        "events": one("SELECT count(*) FROM job_events WHERE job_id = :j"),
    }


def crossings(route: OwnedRoute, mine: Rows, theirs: Rows) -> list[tuple[str, dict[str, Any]]]:
    """The id sets a caller ``mine`` tries: everything the other user's; and, where the path has a
    class and a revision, each mixed with one of the caller's own."""
    sets: list[tuple[str, dict[str, Any]]] = [("all theirs", theirs.params())]
    if "{class_id}" in route.template and "{revision_id}" in route.template:
        sets.append(("my class, their revision", theirs.params() | {"class_id": mine.class_id}))
        sets.append(
            ("their class, my revision", theirs.params() | {"revision_id": mine.revision_id})
        )
    return sets


@pytest.mark.parametrize("route", OWNED_ROUTES, ids=lambda r: f"{r.method} {r.template}")
async def test_owned_routes_answer_404_for_another_users_ids(
    route: OwnedRoute, env: Env, browsers: Any, make_actor: Any, superuser: Connection
) -> None:
    alice_rows = await plant(env, superuser, (await browsers(mowl=False)).user_id)
    bob_rows = await plant(env, superuser, (await browsers(mowl=False)).user_id)
    alice: Actor = await make_actor(alice_rows.user)
    bob: Actor = await make_actor(bob_rows.user)
    unknown = {n: uuid.UUID(UNKNOWN) for n in OWNED_PATH_PARAMS}
    body = route.body(alice_rows) if route.body else None
    # What an id that exists nowhere gets is the reference answer.
    reference = await bob.call(route.method, fill(route.template, **unknown), body)
    assert reference.status_code == 404, reference.text
    before = owned_state(superuser, alice_rows), owned_state(superuser, bob_rows)

    for label, ids in crossings(route, bob_rows, alice_rows):
        foreign = await bob.call(route.method, fill(route.template, **ids), body)
        assert foreign.status_code == 404, (label, foreign.status_code, foreign.text)
        if foreign.headers.get("content-type", "").startswith("application/json"):
            assert foreign.json() == reference.json(), (label, foreign.text, reference.text)
    if "{class_id}" in route.template and "{revision_id}" in route.template:
        mixed = bob_rows.params() | {"revision_id": alice_rows.revision_id}
        answer = await bob.call(route.method, fill(route.template, **mixed), body)
        assert answer.status_code == 404, answer.text
        mixed = alice_rows.params() | {"class_id": bob_rows.class_id}
        answer = await alice.call(route.method, fill(route.template, **mixed), body)
        assert answer.status_code == 404, answer.text
    assert (owned_state(superuser, alice_rows), owned_state(superuser, bob_rows)) == before

    # The owner's own call is not a 404: the ids were real.
    if route.control is not None:
        own = await alice.call(route.method, fill(route.template, **alice_rows.params()), body)
        assert own.status_code in route.control, (own.status_code, own.text)
    else:
        sibling = await alice.get(f"/api/external-operations/{alice_rows.operation_id}")
        assert sibling.status_code == 200, sibling.text


async def test_the_external_operations_list_never_shows_another_users_operation(
    env: Env, browsers: Any, make_actor: Any, superuser: Connection
) -> None:
    alice_rows = await plant(env, superuser, (await browsers(mowl=False)).user_id)
    bob_rows = await plant(env, superuser, (await browsers(mowl=False)).user_id)
    alice: Actor = await make_actor(alice_rows.user)
    bob: Actor = await make_actor(bob_rows.user)
    by_job = await bob.get(f"/api/external-operations?job_id={alice_rows.job_id}")
    assert by_job.status_code in (200, 404), by_job.text
    assert str(alice_rows.operation_id) not in by_job.text
    assert str(alice_rows.operation_id) not in (await bob.get("/api/external-operations")).text
    assert str(alice_rows.operation_id) in (await alice.get("/api/external-operations")).text


async def test_a_job_create_naming_another_users_rows_creates_nothing(
    env: Env, browsers: Any, superuser: Connection
) -> None:
    """The one ``owned_body`` route: the job input names a class and a connection. Another
    user's ids are refused like ids that exist nowhere, and create no job, class or credit."""
    alice = await browsers()
    bob = await browsers()
    alice_class = new_class(superuser, alice.user_id)

    def rows() -> tuple[int, int, int]:
        return tuple(  # type: ignore[return-value]
            superuser.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in ("jobs", "classes", "credit_ledger")
        )

    before = rows()
    own = await bob.create()
    assert own.status_code == 201, own.text  # the route works for Bob's own ids
    after_own = rows()
    assert after_own == (before[0] + 1, before[1] + 1, before[2] + 1)

    nowhere_connection = await bob.create(mowl_connection_id=UNKNOWN)
    stolen_connection = await bob.create(mowl_connection_id=str(alice.connection_id))
    nowhere_class = await bob.create(class_id=UNKNOWN)
    stolen_class = await bob.create(class_id=str(alice_class))
    assert nowhere_connection.status_code in (404, 409, 422), nowhere_connection.text
    assert (stolen_connection.status_code, stolen_connection.json()) == (
        nowhere_connection.status_code, nowhere_connection.json()
    ), stolen_connection.text  # fmt: skip
    assert nowhere_class.status_code in (404, 409, 422), nowhere_class.text
    assert (stolen_class.status_code, stolen_class.json()) == (
        nowhere_class.status_code, nowhere_class.json()
    ), stolen_class.text  # fmt: skip
    assert rows() == after_own


async def test_the_real_job_kinds_refuse_another_users_revision_and_spotify_connection(
    env: Env, browsers: Any, superuser: Connection
) -> None:
    """Save names a revision, a class and a MOWL connection; a song pick names a Spotify and a
    MOWL connection. Another user's id in any of them is refused with the answer an id that
    exists nowhere gets (the real kinds, not the test kind of ``browsers``)."""
    from classbeat import connections, saving, songs
    from ctx import make_context
    from songtools import BRIEF

    alice = await browsers()
    bob = await browsers()
    env.app.dependency_overrides.clear()  # the real registry
    theirs = await plant(env, superuser, alice.user_id)
    mine = await plant(env, superuser, bob.user_id)
    spotify = await connections.save(
        env.factory,
        env.app.state.keyring,
        make_context(alice.user_id),
        "spotify",
        {"access_token": "at-" + "a" * 20, "refresh_token": "rt-" + "b" * 20, "expires_at": 1},
        public={"client_id": "a" * 32, "spotify_user_id": "rider"},
        confirm_account_change=True,
    )
    superuser.execute(
        text("INSERT INTO entitlements (owner_id, features, source) "
             "VALUES (:o, ARRAY['song_picking'], 'grant')"),
        {"o": bob.user_id},
    )  # fmt: skip

    def rows() -> tuple[int, int, int]:
        return tuple(  # type: ignore[return-value]
            superuser.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
            for t in ("jobs", "classes", "credit_ledger")
        )

    async def save(revision_id: object) -> Any:
        body = {
            "kind": saving.KIND,
            "input": {
                "class_id": str(mine.class_id),
                "revision_id": str(revision_id),
                "content_hash": mine.digest,
                "mowl_connection_id": str(bob.connection_id),
            },
        }
        return await bob.post("/api/jobs", body, {"Idempotency-Key": uuid.uuid4().hex})

    async def pick(spotify_id: object) -> Any:
        body = {
            "kind": songs.KIND,
            "input": {
                "title": "x",
                "brief": BRIEF,
                "mowl_connection_id": str(bob.connection_id),
                "spotify_connection_id": str(spotify_id),
            },
        }
        return await bob.post("/api/jobs", body, {"Idempotency-Key": uuid.uuid4().hex})

    before = rows()
    nowhere_revision = await save(UNKNOWN)
    stolen_revision = await save(theirs.revision_id)
    nowhere_spotify = await pick(UNKNOWN)
    stolen_spotify = await pick(spotify)
    assert nowhere_revision.status_code in (404, 409, 422), nowhere_revision.text
    assert (stolen_revision.status_code, stolen_revision.json()) == (
        nowhere_revision.status_code, nowhere_revision.json()
    ), stolen_revision.text  # fmt: skip
    assert nowhere_spotify.status_code in (404, 409, 422), nowhere_spotify.text
    assert (stolen_spotify.status_code, stolen_spotify.json()) == (
        nowhere_spotify.status_code, nowhere_spotify.json()
    ), stolen_spotify.text  # fmt: skip
    for refused in (stolen_revision, stolen_spotify):
        assert str(theirs.revision_id) not in refused.text
        assert str(spotify) not in refused.text
    assert rows() == before
    # Bob's own revision is not refused the same way (the check is about ownership).
    own = await save(mine.revision_id)
    assert own.status_code != nowhere_revision.status_code or own.json() != nowhere_revision.json()
