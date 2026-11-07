"""Guard of ADR 0025 section 1: every route reachable without a session that changes state is
either behind Turnstile or exempt with its reason.

The app's route table is the input. Every unsafe method of a route with no session requirement
(``require_user``, ``require_recent_auth``, ``require_stepup``, ``require_admin``, the admin gate),
plus ``GET /oauth/{provider}/callback`` (it creates accounts and sessions with a safe method), must
be in ``TURNSTILE`` or ``EXEMPT``, and nothing may be listed that the app no longer serves. It is an
allowlist: a new anonymous route, the coming contact form included, fails here until it is
classified. Each Turnstile route's handler calls ``require_captcha`` with its own action, and
``test_turnstile_routes.py`` shows each one refuses a request without a token when it is on.
"""

import inspect
from typing import Any

from fastapi.routing import APIRoute, iter_route_contexts

from classbeat.admin.routes import _calls, admin_gate  # pyright: ignore[reportPrivateUsage]
from classbeat.auth import deps as auth_deps
from classbeat.web import create_app
from test_m6_security_routes import _settings  # pyright: ignore[reportPrivateUsage]

# (method, path) -> the Turnstile action (ADR 0025 section 1).
TURNSTILE: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): "signin",
    ("POST", "/api/auth/signup"): "signup",
    ("POST", "/api/auth/oauth/pending/email"): "signup_email",
    ("POST", "/api/auth/password/reset/request"): "reset",
}
# (method, path) -> why it needs no Turnstile token (ADR 0025 section 1).
EXEMPT: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/oauth/{provider}/start"): (
        "touches no account or mailbox: a 10-minute flow row, per-IP limited; the provider's own "
        "sign-in checks the person, and the mail-sending step after it (pending/email) is covered"
    ),
    ("GET", "/oauth/{provider}/callback"): (
        "single-use flow taken atomically with state, PKCE and nonce; its start is per-IP limited "
        "and the provider checked the person"
    ),
    ("POST", "/api/auth/signup/confirm"): "the 32-byte mailed token is the proof",
    ("POST", "/api/auth/password/reset/info"): "the 32-byte mailed token is the proof",
    ("POST", "/api/auth/password/reset/complete"): "the 32-byte mailed token is the proof",
    ("POST", "/api/mail/unsubscribe"): "a signed token; changes only that mail preference",
    ("POST", "/api/mail/unsubscribe/confirm"): "a signed token; changes only that mail preference",
    ("POST", "/api/auth/logout"): (
        "ends the caller's own session; without one it does nothing (no account, mail or session)"
    ),
}
SESSION_REQUIRED = {
    auth_deps.require_user,
    auth_deps.require_recent_auth,
    auth_deps.require_stepup,
    auth_deps.require_admin,
    admin_gate,
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Safe methods that change state without a session all the same.
STATEFUL_SAFE = {("GET", "/oauth/{provider}/callback")}


def _routes(app: Any) -> dict[tuple[str, str], APIRoute]:
    found: dict[tuple[str, str], APIRoute] = {}
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            path = context.path or route.path
            for method in route.methods or set():
                found[(method, path)] = route
    return found


def anonymous_state_changing(app: Any) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for (method, path), route in _routes(app).items():
        if _calls(route.dependant) & SESSION_REQUIRED:
            continue
        if method in UNSAFE_METHODS or (method, path) in STATEFUL_SAFE:
            found.add((method, path))
    return found


def test_every_anonymous_state_changing_route_is_classified() -> None:
    # The full app: admin area and song picking on, as the M6 route matrix builds it.
    found = anonymous_state_changing(create_app(_settings()))
    classified = TURNSTILE.keys() | EXEMPT.keys()
    assert not TURNSTILE.keys() & EXEMPT.keys()
    unclassified = sorted(found - classified)
    stale = sorted(classified - found)
    assert unclassified == [], (
        f"anonymous routes that change state without a Turnstile decision: {unclassified}; add "
        "each to TURNSTILE (and call require_captcha) or to EXEMPT with its reason (ADR 0025 s. 1)"
    )
    assert stale == [], f"classified routes that are gone or now need a session: {stale}"
    assert len(found) >= 12  # not vacuous


def test_the_same_holds_with_turnstile_on() -> None:
    on = _settings(turnstile_enabled=True, turnstile_fake=True)
    assert anonymous_state_changing(create_app(on)) == TURNSTILE.keys() | EXEMPT.keys()


def test_each_turnstile_route_calls_the_check_with_its_own_action_first() -> None:
    routes = _routes(create_app(_settings()))
    for key, action in TURNSTILE.items():
        source = inspect.getsource(routes[key].endpoint)
        call = f'await require_captcha(request, "{action}", body.captcha_token)'
        assert source.count("require_captcha(") == 1, key
        assert call in source, key
        body = source.split(":\n", 1)[1]  # the handler's body, after its signature
        statements = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        # Before it only checks of the body alone (sign-up's password rule and the address's
        # normal form), never a database, a bucket, the hasher or a cookie.
        before = statements[: statements.index(call)]
        for line in before:
            assert not any(
                word in line
                for word in ("system_session", "reserve(", "hasher", "cookies", "mailer")
            ), (key, line)
