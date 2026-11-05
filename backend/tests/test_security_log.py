"""The privacy-safe security log (#632, ADR 0003 amendment "security log" of 2026-11-05).

One ``security_log`` row per signed-in ``/api`` request: the user, the session, a keyed hash of
the coarse network, the method, the route template, the status and the time. Never the address,
``User-Agent``, a token, the query string or the body. Fakes only: the client address comes from
``X-Forwarded-For`` behind a trusted loopback proxy.
"""

import ast
import dataclasses
import logging
import re
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID

import pytest
from fastapi import Depends, Request
from sqlalchemy import create_engine, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import Engine

from authtools import ORIGIN, Env, running_env
from classbeat import auth
from classbeat.auth import securitylog
from classbeat.auth.deps import require_user
from classbeat.auth.keys import Keys, hmac_digest
from classbeat.context import UserContext
from classbeat.db import user_session
from ctx import make_context
from flows import Browser, user_with_password

pytestmark = pytest.mark.usefixtures("frozen_rate_windows")  # #588: no window edge in a test

SRC = Path(__file__).resolve().parent.parent / "src" / "classbeat" / "auth" / "securitylog.py"
ITEM = "/api/test/items/{item_id}"
HOME = "198.51.100.7"
# Planted in every request; none may reach the table.
AGENT = "Mozilla/5.0 CanaryBrowser/9.9 (agent-canary-7341)"
PATH_ID = "path-canary-5512"
QUERY = "query-canary-8810"
BODY = "body-canary-2207"


class Client(Browser):
    """A browser at a client address with a ``User-Agent``."""

    def __init__(self, env: Env, ip: str = HOME) -> None:
        super().__init__(env)
        self.ip = ip

    def _cookie_header(self) -> dict[str, str]:
        return {**super()._cookie_header(), "X-Forwarded-For": self.ip, "User-Agent": AGENT}


@pytest.fixture
async def env(clean_auth: None, app_database_url: str) -> AsyncIterator[Env]:
    """An app behind a trusted loopback proxy, with a signed-in route that has a path parameter."""
    async with running_env(app_database_url, trusted_proxies="127.0.0.1") as running:

        @running.app.get(ITEM)
        async def item(  # pyright: ignore[reportUnusedFunction]
            item_id: str, ctx: Annotated[UserContext, Depends(require_user)]
        ) -> dict[str, bool]:
            return {"ok": True}

        @running.app.post(ITEM)
        async def change(  # pyright: ignore[reportUnusedFunction]
            item_id: str, request: Request, ctx: Annotated[UserContext, Depends(require_user)]
        ) -> dict[str, bool]:
            await request.body()
            return {"ok": True}

        yield running


@pytest.fixture
def admin(database_url: str) -> Iterator[Engine]:
    """The superuser, to read every row whatever the owner."""
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def rows(admin: Engine) -> list[Any]:
    with admin.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT owner_id, session_id, ip_hash, method, route, status, at "
                    "FROM security_log ORDER BY at, id"
                )
            ).all()
        )


def dump(admin: Engine) -> str:
    """Every row as text, the hash in hex and as raw bytes decoded leniently."""
    with admin.connect() as connection:
        found = connection.execute(
            text("SELECT row_to_json(s)::text, ip_hash FROM security_log s")
        ).all()
    return "\n".join(f"{row[0]} {bytes(row[1]).decode('latin-1')}" for row in found)


async def signed_in(env: Env, ip: str = HOME) -> Client:
    client = Client(env, ip)
    await user_with_password(env)
    assert (await client.sign_in("user@example.com")).status_code == 200
    return client


# --- what a row holds --------------------------------------------------------------------------


def test_an_entry_holds_only_the_allowed_fields() -> None:
    """The allowlist: adding a field (an address, an agent, a path) fails here and in review."""
    names = [field.name for field in dataclasses.fields(securitylog.Entry)]
    assert names == ["user_id", "session_id", "ip_hash", "method", "route", "status"]
    entry = securitylog.Entry(UUID(int=1), UUID(int=2), b"\x01" * 32, "GET", "/api/x", 200)
    assert repr(entry) == "Entry(GET /api/x 200)"  # never the hash


def test_the_module_reads_no_header_query_cookie_or_body() -> None:
    """``securitylog`` itself never touches what must not be logged; the address is read only
    through ``request_ip`` and hashed at once (docstrings and comments are not code)."""
    tree = ast.parse(SRC.read_text("utf-8"))
    forbidden = {"headers", "cookies", "query_params", "query", "body", "json", "form", "url"}
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } & forbidden
    assert attributes <= {"url"}
    # ``url`` only ever as ``request.url.path``: the path is matched and tested, never stored.
    parents = {
        id(node.value): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "url":
            assert parents[id(node)].attr == "path"
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    strings = [
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]  # fmt: skip
    for word in ("user-agent", "cookie", "authorization", "x-forwarded-for", "query"):
        assert not any(word in value for value in strings), word


def test_the_network_is_a_keyed_hash_of_the_coarse_address_under_its_own_key() -> None:
    keys = Keys.from_secret(b"s" * 32)
    one = securitylog.network_hash(keys, "2001:db8:1:2::10")
    assert one == securitylog.network_hash(keys, "2001:db8:1:2:aaaa::1")  # same /64
    assert one != securitylog.network_hash(keys, "2001:db8:1:3::10")
    assert one == hmac_digest(keys.security_log, "2001:db8:1:2::/64")
    assert one != hmac_digest(keys.session_client, "2001:db8:1:2::/64")  # not the session's key
    assert securitylog.network_hash(keys, HOME) != securitylog.network_hash(keys, "203.0.113.4")
    assert len(one) == 32


def test_every_api_route_has_a_template_the_log_accepts_and_finds() -> None:
    """No route of ours falls back to ``unmatched``: each template has the allowed shape, and a
    path of that shape is matched back to it for each of its methods."""
    from authtools import make_settings
    from classbeat.web import create_app

    app = create_app(make_settings("postgresql+psycopg://u:p@127.0.0.1:1/x"))
    paths = {
        template: operations
        for template, operations in app.openapi()["paths"].items()
        if template.startswith("/api")
    }
    assert len(paths) > 50
    for template, operations in paths.items():
        assert len(template) <= securitylog.ROUTE_MAX, template
        assert securitylog.ROUTE.fullmatch(template), template
        sample = re.sub(r"\{[a-z_0-9]+\}", "v1", template)
        for method in operations:
            found = securitylog.match_template(app, method.upper(), sample)
            # A literal route registered first may take the sample (``/api/x/v1`` is no literal).
            assert found == template or "{" not in found, (method, template, found)
    assert securitylog.match_template(app, "GET", "/api/no/such/thing") == "unmatched"
    assert (
        securitylog.match_template(app, "GET", "/api/auth/sessions?x=1") == "unmatched"
    )  # a path never holds a query; nothing of the client's text is returned


def _served_routes(routes: Iterable[Any]) -> Iterator[tuple[str, frozenset[str], bool]]:
    """``(template, methods, in schema)`` of every endpoint the app serves, included routers'
    routes with their prefixes, whether or not they are in the OpenAPI schema."""
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is not None:
            for context in contexts():
                original = context.original_route
                yield (
                    context.path_format,
                    frozenset(getattr(original, "methods", None) or ()),
                    bool(getattr(original, "include_in_schema", True)),
                )
        elif isinstance(getattr(route, "path_format", None), str):
            yield (
                route.path_format,
                frozenset(getattr(route, "methods", None) or ()),
                bool(getattr(route, "include_in_schema", True)),
            )


# FastAPI's own documentation pages (development only): not ``APIRoute``s, logged as unmatched.
DOCS_ROUTES = frozenset({"/api/openapi.json", "/api/docs"})


def test_no_api_route_is_hidden_from_the_log() -> None:
    """Every ``/api`` endpoint in ``app.routes`` (``include_in_schema=False`` ones too) is matched
    to its own template, so a hidden route cannot silently log as ``unmatched``."""
    from authtools import make_settings
    from classbeat.web import create_app

    app = create_app(make_settings("postgresql+psycopg://u:p@127.0.0.1:1/x"))
    served = [route for route in _served_routes(app.routes) if route[0].startswith("/api")]
    assert len(served) > 50
    for template, methods, in_schema in served:
        if template in DOCS_ROUTES:
            continue
        assert in_schema, f"{template} is hidden from the schema and so from the security log"
        sample = re.sub(r"\{[a-z_0-9]+\}", "v1", template)
        for method in methods - {"HEAD"}:
            found = securitylog.match_template(app, method, sample)
            assert found == template or "{" not in found, (method, template, found)


def test_the_routes_are_not_cached_when_the_schema_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing ``openapi()`` is logged by class and tried again on the next request; an empty
    list is never cached (it would log every request as ``unmatched`` for the process's life)."""

    class App:
        def __init__(self) -> None:
            self.state = SimpleNamespace()
            self.calls = 0

        def openapi(self) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("schema-canary-4410")
            return {"paths": {"/api/x/{x_id}": {"get": {}}}}

    app = App()
    with caplog.at_level(logging.ERROR, logger="classbeat.auth.securitylog"):
        assert securitylog.match_template(app, "GET", "/api/x/1") == "unmatched"
    assert [r.getMessage() for r in caplog.records] == [
        "security log routes unavailable: RuntimeError"
    ]
    assert securitylog.match_template(app, "GET", "/api/x/1") == "/api/x/{x_id}"
    assert securitylog.match_template(app, "GET", "/api/x/2") == "/api/x/{x_id}"
    assert app.calls == 2  # cached once it worked


# --- requests ----------------------------------------------------------------------------------


async def test_a_signed_in_request_writes_its_template_and_nothing_it_must_not(
    env: Env, admin: Engine
) -> None:
    client = await signed_in(env)
    token = client.cookies[auth.SESSION_COOKIE]
    assert (await client.get(f"/api/test/items/{PATH_ID}?secret={QUERY}")).status_code == 200
    response = await client.post(f"/api/test/items/{PATH_ID}?q={QUERY}", {"note": BODY})
    assert response.status_code == 200
    found = [row for row in rows(admin) if row.route == ITEM]
    assert [(row.method, row.status) for row in found] == [("GET", 200), ("POST", 200)]
    session = await env.row("SELECT id, user_id FROM sessions")
    for row in found:
        assert (row.owner_id, row.session_id) == (session.user_id, session.id)
        assert bytes(row.ip_hash) == securitylog.network_hash(env.app.state.keys, HOME)
    text_of_table = dump(admin)
    for canary in (HOME, AGENT, "agent-canary", PATH_ID, QUERY, BODY, token, "secret="):
        assert canary not in text_of_table, canary
    # Every row has the shape of the allowlist: no raw path, no query.
    for row in rows(admin):
        assert row.route == securitylog.UNMATCHED or securitylog.ROUTE.fullmatch(row.route)


async def test_anonymous_and_non_api_requests_write_nothing(env: Env, admin: Engine) -> None:
    anonymous = Client(env)
    assert (await anonymous.get("/api/auth/session")).status_code == 200
    assert (await anonymous.get(f"/api/test/items/{PATH_ID}")).status_code == 401
    assert rows(admin) == []
    client = await signed_in(env)
    before = len(rows(admin))
    await client.get("/")  # the SPA, not the API
    assert len(rows(admin)) == before


async def test_refusals_and_unknown_routes_are_logged_with_their_status(
    env: Env, admin: Engine
) -> None:
    client = await signed_in(env)
    assert (await client.get(f"/api/no/such/{PATH_ID}")).status_code == 404
    # A write without the CSRF token: the middleware's own 403.
    env.client.cookies.clear()
    refused = await env.client.post(
        ITEM.replace("{item_id}", PATH_ID),
        headers={"Origin": ORIGIN, **client._cookie_header()},  # pyright: ignore[reportPrivateUsage]
    )
    assert refused.status_code == 403
    found = {(row.method, row.route, row.status) for row in rows(admin)}
    assert ("GET", "unmatched", 404) in found
    assert ("POST", ITEM, 403) in found
    assert PATH_ID not in dump(admin)


async def test_a_rate_limited_request_is_logged_as_429(
    clean_auth: None, app_database_url: str, admin: Engine
) -> None:
    async with running_env(
        app_database_url, trusted_proxies="127.0.0.1", api_requests_per_minute=3
    ) as env:
        client = await signed_in(env)
        statuses = [(await client.get("/api/auth/sessions")).status_code for _ in range(25)]
        assert statuses.count(429) >= 20
        assert ("GET", "/api/auth/sessions", 429) in {
            (row.method, row.route, row.status) for row in rows(admin)
        }
        # T124: past the limit a session adds at most one row a minute, however many requests
        # (two only if the run straddles a minute boundary).
        with admin.connect() as connection:
            limited, minutes = connection.execute(
                text(
                    "SELECT count(*), count(DISTINCT limited_minute) FROM security_log "
                    "WHERE status = 429"
                )
            ).one()
        assert limited == minutes
        assert 1 <= limited <= 2


async def test_a_failed_write_never_changes_the_answer_and_logs_no_values(
    env: Env, admin: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = await signed_in(env)
    # A statement the table refuses (NOT NULL): the request still answers 200.
    monkeypatch.setattr(
        securitylog,
        "_INSERT",
        text("INSERT INTO security_log (owner_id) VALUES (:owner_id)"),
    )
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.securitylog"):
        assert (await client.get(f"/api/test/items/{PATH_ID}")).status_code == 200
    assert rows(admin) == []
    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["security log write failed: IntegrityError (SQLSTATE 23502)"]
    user_id = str(await env.scalar("SELECT id FROM users"))
    assert not any(user_id in message or PATH_ID in message for message in messages)


@pytest.mark.parametrize(
    "error",
    [sa_exc.TimeoutError("QueuePool limit reached; canary-9931"), RuntimeError("canary-9931")],
    ids=["pool-timeout", "other"],
)
async def test_a_non_driver_error_never_changes_the_answer(
    env: Env,
    admin: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """B1 (#641 review): a pool timeout (not a ``DBAPIError``) after the route committed must not
    turn a succeeded request into a 500; it is logged by class only."""
    client = await signed_in(env)

    def unavailable(*args: object, **kwargs: object) -> Any:
        raise error

    monkeypatch.setattr(securitylog, "user_session", unavailable)
    with caplog.at_level(logging.WARNING, logger="classbeat.auth.securitylog"):
        response = await client.post(f"/api/test/items/{PATH_ID}", {"note": BODY})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert rows(admin) == []
    messages = [record.getMessage() for record in caplog.records]
    # The browser fetches its CSRF token first: one failed write per request, each the same.
    assert messages
    assert set(messages) == {f"security log write failed: {type(error).__name__}"}
    assert not any("canary" in message for message in messages)


async def test_a_user_sees_only_their_own_rows(env: Env, admin: Engine) -> None:
    client = await signed_in(env)
    await client.get(f"/api/test/items/{PATH_ID}")
    owner = await env.scalar("SELECT id FROM users")
    assert isinstance(owner, UUID)
    other = await env.user("other@example.com")
    count = text("SELECT count(*) FROM security_log")
    async with user_session(env.factory, make_context(owner)) as db:
        assert (await db.execute(count)).scalar_one() > 0
    async with user_session(env.factory, make_context(other)) as db:
        assert (await db.execute(count)).scalar_one() == 0
    async with env.db() as db:  # no user: row security shows nothing
        assert (await db.execute(count)).scalar_one() == 0


async def test_the_purge_keeps_the_configured_days(
    clean_auth: None, app_database_url: str, admin: Engine
) -> None:
    async with running_env(app_database_url, security_log_days=2) as env:
        owner = await env.user()
        with admin.begin() as connection:
            for days in (3, 1):
                connection.execute(
                    text(
                        "INSERT INTO security_log (owner_id, session_id, ip_hash, method, route, "
                        "status, at) VALUES (:o, gen_random_uuid(), :h, 'GET', '/api/x', 200, "
                        "now() - make_interval(days => :d))"
                    ),
                    {"o": owner, "h": bytes(32), "d": days},
                )
        counts = await auth.purge_expired(env.factory, env.settings)
        assert counts["security_log"] == 1
        [kept] = rows(admin)
        with admin.connect() as connection:
            age = connection.execute(text("SELECT now() - at FROM security_log")).scalar_one()
        assert age.days == 1
        assert kept.owner_id == owner


def test_the_retention_setting_defaults_to_seven_days_and_is_bounded() -> None:
    from authtools import make_settings

    url = "postgresql+psycopg://u:p@127.0.0.1:1/x"
    assert make_settings(url).security_log_days == 7
    for bad in (0, 31):
        with pytest.raises(ValueError, match="security_log_days"):
            make_settings(url, security_log_days=bad)


async def test_deleting_the_account_removes_the_rows(env: Env, admin: Engine) -> None:
    client = await signed_in(env)
    await client.get(f"/api/test/items/{PATH_ID}")
    assert rows(admin)
    await env.run("DELETE FROM users")
    assert rows(admin) == []
