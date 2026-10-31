"""M6 security review SR-01 (#351; threat model T103): a cap on the request body of every web route.

``classbeat.bodylimit.BodyLimit`` refuses a body over 64 KiB with a fixed 413 before any session,
CSRF or database work, by ``Content-Length`` without reading a byte and otherwise by counting what
streams in (a chunked body, or one whose ``Content-Length`` lies). Two parts:

- the middleware on its own, around a stub app (no database): each rule, and that the app never
  runs, or never sees more than the limit, when it must not;
- the real app: the anonymous login endpoint, and every route of the route table (the same table
  ``test_m6_security_routes`` classifies) given a body one byte over the cap.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from authtools import ORIGIN, Env, running_env
from classbeat import bodylimit
from classbeat.bodylimit import BodyLimit
from classbeat.headers import CONTENT_SECURITY_POLICY, REFERRER_POLICY
from classbeat.web import create_app
from test_m6_security_routes import CLASSIFIED, _settings, fill, table_of  # pyright: ignore[reportPrivateUsage]

LIMIT = bodylimit.DEFAULT_BODY_LIMIT
FIXED = b'{"detail":{"reason":"body_too_large"}}'

# --- the middleware around a stub app -----------------------------------------------------------


class Probe:
    """An ASGI app that reads the whole body (as a framework does) and answers 200 with its size."""

    def __init__(self, behaviour: str = "read") -> None:
        self.calls = 0
        self.read = 0
        self.behaviour = behaviour

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        if self.behaviour == "ignore":
            await _answer(send, 200, b"ok")
            return
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                if self.behaviour == "answer400":
                    await _answer(send, 400, b"unreadable")
                    return
                if self.behaviour == "raise":
                    raise RuntimeError("client disconnected")
                if self.behaviour == "groups":
                    raise ExceptionGroup("x", [HTTPException(400)])
                return
            self.read += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await _answer(send, 200, str(self.read).encode())


async def _answer(send: Any, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": body})


def scope_of(
    path: str = "/api/auth/login",
    method: str = "POST",
    length: bytes | None = None,
    kind: str = "http",
    chunked: bool = False,
) -> dict[str, Any]:
    headers = [] if length is None else [(b"content-length", length)]
    if chunked:
        headers.append((b"transfer-encoding", b"chunked"))
    return {"type": kind, "method": method, "path": path, "headers": headers}


async def run(
    app: Any, scope: dict[str, Any], chunks: list[bytes]
) -> tuple[list[dict[str, Any]], list[int]]:
    """Drive ``app`` with ``chunks`` as the body; returns what it sent and which chunk indexes the
    server side was asked for."""
    queue = list(chunks)
    pulled: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if not queue:
            return {"type": "http.disconnect"}
        pulled.append(len(chunks) - len(queue))
        body = queue.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent, pulled


def status_and_body(sent: list[dict[str, Any]]) -> tuple[int, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], body


def refused(sent: list[dict[str, Any]]) -> None:
    assert status_and_body(sent) == (413, FIXED)
    headers = {k.lower(): v for k, v in next(m for m in sent)["headers"]}
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"connection"] == b"close"
    assert headers[b"content-security-policy"] == CONTENT_SECURITY_POLICY.encode()
    assert headers[b"referrer-policy"] == REFERRER_POLICY.encode()
    assert int(headers[b"content-length"]) == len(FIXED)
    assert len([m for m in sent if m["type"] == "http.response.start"]) == 1


async def test_content_length_over_the_limit_is_refused_before_anything_is_read() -> None:
    probe = Probe()
    sent, pulled = await run(
        BodyLimit(probe), scope_of(length=str(LIMIT + 1).encode()), [b"x" * (LIMIT + 1)]
    )
    refused(sent)
    assert probe.calls == 0  # the app never ran
    assert pulled == []  # and not a byte was read


async def test_a_body_with_a_content_length_that_is_not_a_number_is_refused() -> None:
    for junk in (b"-1", b"1e9", b"", b"12 ", b"0x10"):
        probe = Probe()
        sent, _ = await run(BodyLimit(probe), scope_of(length=junk), [b"{}"])
        refused(sent)
        assert probe.calls == 0, junk


async def test_a_chunked_body_over_the_limit_without_a_content_length_is_refused() -> None:
    probe = Probe()
    chunk = b"x" * 8192
    chunks = [chunk] * ((LIMIT // len(chunk)) + 40)  # far past the limit, no Content-Length
    sent, pulled = await run(BodyLimit(probe), scope_of(), chunks)
    refused(sent)
    assert probe.read <= LIMIT  # the app was never handed more than the limit
    assert len(pulled) <= LIMIT // len(chunk) + 1  # and the rest of the body was never pulled


async def test_a_chunked_body_over_the_limit_is_refused_before_the_app_runs() -> None:
    """``Transfer-Encoding: chunked`` (what a browser or uvicorn presents for a body with no
    length): read up to the limit, refused without the app (so before auth) when over it."""
    probe = Probe()
    chunk = b"x" * 8192
    chunks = [chunk] * ((LIMIT // len(chunk)) + 40)
    sent, pulled = await run(BodyLimit(probe), scope_of(chunked=True), chunks)
    refused(sent)
    assert probe.calls == 0
    assert len(pulled) <= LIMIT // len(chunk) + 1


async def test_a_chunked_body_up_to_the_limit_is_replayed_to_the_app_whole() -> None:
    exact = [b"x" * (LIMIT // 4)] * 4
    probe = Probe()
    sent, _ = await run(BodyLimit(probe), scope_of(chunked=True), exact)
    assert status_and_body(sent) == (200, str(LIMIT).encode())
    probe = Probe()
    sent, _ = await run(BodyLimit(probe), scope_of(chunked=True), [*exact, b"x"])
    refused(sent)
    assert probe.calls == 0


async def test_a_lying_content_length_does_not_get_past_the_cap() -> None:
    probe = Probe()
    chunks = [b"x" * 10_000] * 20  # 200 kB streamed under a header that says 10 bytes
    sent, _ = await run(BodyLimit(probe), scope_of(length=b"10"), chunks)
    refused(sent)
    assert probe.read <= LIMIT


async def test_a_body_of_exactly_the_limit_passes_and_one_byte_more_does_not() -> None:
    exact = [b"x" * (LIMIT // 2), b"x" * (LIMIT - LIMIT // 2)]
    for scope in (scope_of(), scope_of(length=str(LIMIT).encode())):
        probe = Probe()
        sent, _ = await run(BodyLimit(probe), scope, exact)
        assert status_and_body(sent) == (200, str(LIMIT).encode())
    probe = Probe()
    sent, _ = await run(BodyLimit(probe), scope_of(), [*exact, b"x"])
    refused(sent)
    probe = Probe()
    sent, _ = await run(BodyLimit(probe), scope_of(length=str(LIMIT + 1).encode()), [*exact, b"x"])
    refused(sent)


@pytest.mark.parametrize("behaviour", ["answer400", "raise", "groups"])
async def test_the_answer_is_the_413_however_the_app_fails_on_a_cut_body(behaviour: str) -> None:
    """FastAPI turns a body that ends early into its own 400, a task group into an exception group:
    neither reaches the client."""
    probe = Probe(behaviour)
    sent, _ = await run(BodyLimit(probe), scope_of(), [b"x" * (LIMIT // 2 + 1)] * 3)
    refused(sent)


async def test_an_error_of_the_app_that_is_not_about_the_body_is_raised() -> None:
    async def failing(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await run(BodyLimit(failing), scope_of(), [b"{}"])


async def test_requests_without_a_body_and_other_scopes_are_unaffected() -> None:
    probe = Probe("ignore")
    sent, _ = await run(BodyLimit(probe), scope_of("/api/classes", "GET"), [])
    assert status_and_body(sent) == (200, b"ok")
    # A big response (the SSE stream, a download) is no business of the body limit.
    big = b"x" * (LIMIT * 4)

    async def streaming(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": big, "more_body": True})
        await send({"type": "http.response.body", "body": big})

    sent, _ = await run(BodyLimit(streaming), scope_of("/api/jobs/x/events", "GET"), [])
    assert status_and_body(sent) == (200, big + big)
    for kind in ("websocket", "lifespan"):
        probe = Probe("ignore")
        await run(BodyLimit(probe), scope_of(kind=kind, length=b"99999999"), [])
        assert probe.calls == 1, kind


async def test_the_admin_area_is_left_to_its_own_limiter_and_nothing_else_is() -> None:
    for path in ("/api/admin/codes", "/api/admin"):
        probe = Probe()
        await run(BodyLimit(probe), scope_of(path, length=b"99999999"), [b"{}"])
        assert probe.calls == 1, path  # AdminBodyLimit answers there, behind the gate
    for path in ("/api/administrator", "/api/adminx/y", "/", "/oauth/google/callback"):
        probe = Probe()
        sent, _ = await run(BodyLimit(probe), scope_of(path, length=b"99999999"), [])
        refused(sent)
        assert probe.calls == 0, path


async def test_a_route_with_its_own_limit_gets_it_and_the_others_keep_the_default() -> None:
    app = BodyLimit(Probe(), routes={"/api/big": 4 * LIMIT})
    sent, _ = await run(app, scope_of("/api/big"), [b"x" * (3 * LIMIT)])
    assert status_and_body(sent)[0] == 200
    sent, _ = await run(app, scope_of("/api/big"), [b"x" * (4 * LIMIT + 1)])
    refused(sent)
    sent, _ = await run(app, scope_of("/api/other"), [b"x" * (LIMIT + 1)])
    refused(sent)


def test_no_route_has_a_limit_of_its_own_yet() -> None:
    """The largest body a route takes is the job create's input (16 KiB), under the 64 KiB cap.
    The first route that needs more adds its entry (and its reason) to ``ROUTE_LIMITS`` and
    here."""
    assert dict(bodylimit.ROUTE_LIMITS) == {}
    assert bodylimit.DEFAULT_BODY_LIMIT == 64 * 1024


def test_the_cap_equals_the_admin_and_jobs_caps_and_covers_the_job_input() -> None:
    from classbeat.admin.routes import MAX_BODY_BYTES
    from classbeat.jobs.routes import BODY_MAX_BYTES, INPUT_MAX_BYTES

    assert LIMIT == MAX_BODY_BYTES == BODY_MAX_BYTES
    assert INPUT_MAX_BYTES * 2 <= LIMIT  # room for the JSON around the input


# --- the real app -------------------------------------------------------------------------------


@pytest.fixture
async def env(app_database_url: str, maint_database_url: str) -> AsyncIterator[Env]:
    _ = maint_database_url  # installs the queue functions and roles the app checks at start
    async with running_env(app_database_url, song_picking=True) as running:
        yield running


def padded(size: int) -> bytes:
    prefix, suffix = b'{"email":"a@example.com","password":"', b'"}'
    return prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix


def check_refused_response(response: httpx.Response) -> None:
    assert response.status_code == 413
    assert response.content == FIXED
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["referrer-policy"] == REFERRER_POLICY
    assert response.headers["cache-control"] == "no-store"


async def test_an_anonymous_oversized_login_is_refused_before_auth_and_csrf(env: Env) -> None:
    """No session cookie, no CSRF header, no Origin: a CSRF refusal (403) would mean the auth
    middleware ran first. Over the cap it is the 413, with the security headers."""
    over = padded(LIMIT + 1)
    response = await env.client.post(
        "/api/auth/login", content=over, headers={"Content-Type": "application/json"}
    )
    check_refused_response(response)
    # The same request under the cap is the auth middleware's to refuse (no CSRF token: 403).
    under = await env.client.post(
        "/api/auth/login", content=padded(LIMIT), headers={"Content-Type": "application/json"}
    )
    assert under.status_code == 403


async def test_an_anonymous_login_with_csrf_and_origin_is_still_refused_at_the_cap(
    env: Env,
) -> None:
    csrf = (await env.client.get("/api/auth/session")).json()["csrf_token"]
    headers = {"Content-Type": "application/json", "Origin": ORIGIN, "X-CSRF-Token": csrf}
    response = await env.client.post("/api/auth/login", content=padded(LIMIT + 1), headers=headers)
    check_refused_response(response)
    response = await env.client.post("/api/auth/login", content=padded(LIMIT), headers=headers)
    assert response.status_code == 422  # read and validated (the password is too long), not 413


async def test_an_oversized_chunked_login_without_a_content_length_is_refused(env: Env) -> None:
    sent = 0

    async def chunks() -> AsyncIterator[bytes]:
        nonlocal sent
        yield b'{"email":"a@example.com","password":"'
        for _ in range(64):  # 4 MiB offered
            sent += 1
            yield b"x" * 65536

    request = env.client.build_request(
        "POST", "/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert "content-length" not in request.headers
    check_refused_response(await env.client.send(request))
    assert sent <= 3  # the rest of the body was never pulled


async def test_every_route_with_a_body_refuses_one_byte_over_the_cap(env: Env) -> None:
    """The route table is the input: every route of the app but ``/api/admin`` (which its own
    limiter answers behind the gate: ``test_m5_security_admin_bodies``) gets a body of exactly the
    cap plus one byte from an anonymous caller and answers the fixed 413, whatever its method,
    whatever it would have said to the request itself (401, 403, 404, 422), so a new route is
    covered without being listed. One byte less is the route's own business (never a 413)."""
    table = sorted(k for k in table_of(env.app) if not k[1].startswith("/api/admin"))
    assert len(table) >= 50  # not vacuous
    assert {m for m, _ in table} >= {"GET", "POST", "PUT", "DELETE"}
    problems: list[str] = []
    for method, template in table:
        path = fill(
            template,
            provider="google",
            job_id="0" * 32,
            class_id="0" * 32,
            revision_id="0" * 32,
            session_id="0" * 32,
            identity_id="0" * 32,
            operation_id="0" * 32,
            key="x",
        )
        over = await env.client.request(method, path, content=b"x" * (LIMIT + 1))
        if over.status_code != 413 or over.content != FIXED:
            problems.append(f"{method} {template}: {over.status_code} {over.text[:80]}")
        chunked = env.client.build_request(method, path, content=_chunks(LIMIT + 1))
        chunked_answer = await env.client.send(chunked)
        if chunked_answer.status_code != 413 or chunked_answer.content != FIXED:
            problems.append(f"{method} {template} (chunked): {chunked_answer.status_code}")
        at_cap = await env.client.request(method, path, content=b"x" * LIMIT)
        if at_cap.status_code == 413:
            problems.append(f"{method} {template}: 413 at exactly the cap")
    assert problems == []


def _chunks(size: int) -> AsyncIterator[bytes]:
    async def chunks() -> AsyncIterator[bytes]:
        left = size
        while left > 0:
            step = min(left, 4096)
            left -= step
            yield b"x" * step

    return chunks()


async def test_a_get_without_a_body_and_the_health_routes_are_unaffected(env: Env) -> None:
    for path in ("/api/health", "/api/auth/session", "/api/settings/public"):
        response = await env.client.get(path)
        assert response.status_code == 200, path


def test_the_body_limit_is_installed_inside_the_security_headers_and_outside_auth() -> None:
    """Order of the app's middleware, outermost first: a 413 carries the headers (inside
    ``SecurityHeaders``) and comes before session and CSRF work (outside ``AuthMiddleware``)."""
    app = create_app(_settings())
    names = [getattr(m.cls, "__name__", "") for m in app.user_middleware]
    assert names.index("SecurityHeaders") < names.index("BodyLimit")
    assert names.index("BodyLimit") < names.index("AuthMiddleware")
    assert names.index("BodyLimit") < names.index("AdminBodyLimit")


def test_the_classification_still_covers_routes_with_bodies() -> None:
    assert any(m in {"POST", "PUT", "PATCH"} for m, _ in CLASSIFIED)


# --- the log line of a refusal (#360) -----------------------------------------------------------

SECRET_BODY = b"SECRETBODY-" + b"x" * LIMIT


@pytest.fixture(autouse=True)
def _fresh_log_state(monkeypatch: pytest.MonkeyPatch) -> None:
    bodylimit._logged.clear()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(bodylimit, "log_failures", 0)
    monkeypatch.setattr(bodylimit, "_failure_reported", -bodylimit.LOG_INTERVAL_SECONDS)


class Clock:
    now = 1000.0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> type[Clock]:
    Clock.now = 1000.0
    monkeypatch.setattr(bodylimit.time, "monotonic", lambda: Clock.now)
    return Clock


def refusal_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == bodylimit.__name__]


def hostile_scope(client: str = "203.0.113.9", forwarded: bytes | None = None) -> dict[str, Any]:
    headers = [
        (b"content-length", str(len(SECRET_BODY)).encode()),
        (b"cookie", b"__Host-classbeat_session=SESSIONSECRET"),
        (b"authorization", b"Bearer TOKENSECRET"),
        (b"x-csrf-token", b"CSRFSECRET"),
    ]
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded))
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/password/reset/complete",
        "query_string": b"code=OAUTHCODESECRET&state=STATESECRET",
        "headers": headers,
        "client": (client, 50000),
    }


async def test_a_refused_body_is_logged_with_method_path_status_and_address_only(
    caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    caplog.set_level(logging.INFO)
    await run(BodyLimit(Probe()), hostile_scope(), [SECRET_BODY])
    (record,) = refusal_records(caplog)
    assert record.levelno == logging.WARNING
    assert (record.method, record.path, record.status, record.client_ip) == (  # type: ignore[attr-defined]
        "POST",
        "/api/auth/password/reset/complete",
        413,
        "203.0.113.9",
    )
    from classbeat.log import JsonFormatter

    line = JsonFormatter().format(record)
    for secret in ("SECRETBODY", "SESSIONSECRET", "TOKENSECRET", "CSRFSECRET", "OAUTHCODESECRET",
                   "STATESECRET", "code=", "?"):  # fmt: skip
        assert secret not in line, secret
    assert set(json.loads(line)) == {
        "ts", "level", "logger", "message", "method", "path", "status", "client_ip", "suppressed",
    }  # fmt: skip


@pytest.mark.parametrize("how", ["declared", "chunked", "streamed"])
async def test_every_way_of_being_refused_is_logged(
    how: str, caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    scope = hostile_scope()
    if how == "chunked":
        scope["headers"] = [(b"transfer-encoding", b"chunked")]
    if how == "streamed":
        scope["headers"] = []
    await run(BodyLimit(Probe()), scope, [b"x" * 40_000] * 3)
    assert len(refusal_records(caplog)) == 1


async def test_an_accepted_body_and_a_request_without_one_are_not_logged(
    caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    await run(BodyLimit(Probe()), scope_of(), [b"{}"])
    await run(BodyLimit(Probe("ignore")), scope_of("/api/classes", "GET"), [])
    assert refusal_records(caplog) == []


async def test_the_log_line_is_rate_limited_per_address_and_counts_what_it_skipped(
    caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    app = BodyLimit(Probe())
    for _ in range(5):
        await run(app, hostile_scope("203.0.113.9"), [SECRET_BODY])
    await run(app, hostile_scope("203.0.113.10"), [SECRET_BODY])  # another address: its own line
    assert [r.client_ip for r in refusal_records(caplog)] == ["203.0.113.9", "203.0.113.10"]  # type: ignore[attr-defined]
    clock.now += bodylimit.LOG_INTERVAL_SECONDS
    await run(app, hostile_scope("203.0.113.9"), [SECRET_BODY])
    last = refusal_records(caplog)[-1]
    assert (last.client_ip, last.suppressed) == ("203.0.113.9", 4)  # type: ignore[attr-defined]


async def test_an_ipv6_address_is_keyed_by_its_slash_64(
    caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    """One holder of a /64 logs once per interval, not once per address, and cannot flush the
    table with fresh addresses; another /64 has its own line, and the line names the address."""
    app = BodyLimit(Probe())
    for n in range(5):
        await run(app, hostile_scope(f"2001:db8:1:2::{n:x}"), [SECRET_BODY])
    assert len(bodylimit._logged) == 1  # pyright: ignore[reportPrivateUsage]
    (first,) = refusal_records(caplog)
    assert first.client_ip == "2001:db8:1:2::"  # type: ignore[attr-defined]
    await run(app, hostile_scope("2001:db8:1:3::1"), [SECRET_BODY])
    assert len(refusal_records(caplog)) == 2
    clock.now += bodylimit.LOG_INTERVAL_SECONDS
    await run(app, hostile_scope("2001:db8:1:2:ffff::9"), [SECRET_BODY])
    last = refusal_records(caplog)[-1]
    assert (last.client_ip, last.suppressed) == ("2001:db8:1:2:ffff::9", 4)  # type: ignore[attr-defined]


class RaisingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise RuntimeError("the sink is broken")


class RaisingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        raise RuntimeError("the filter is broken")


@pytest.mark.parametrize("broken", ["handler", "filter"])
@pytest.mark.parametrize("how", ["declared", "chunked", "streamed"])
async def test_a_failing_log_call_never_turns_the_413_into_a_500(
    broken: str, how: str, monkeypatch: pytest.MonkeyPatch, clock: type[Clock]
) -> None:
    log = logging.getLogger(bodylimit.__name__)
    monkeypatch.setattr(log, "propagate", False)
    monkeypatch.setattr(log, "handlers", [])
    monkeypatch.setattr(log, "filters", [])
    if broken == "handler":
        log.addHandler(RaisingHandler())
    else:
        log.addFilter(RaisingFilter())
        log.addHandler(logging.NullHandler())
    scope = hostile_scope()
    if how == "chunked":
        scope["headers"] = [(b"transfer-encoding", b"chunked")]
    if how == "streamed":
        scope["headers"] = []
    sent, _ = await run(BodyLimit(Probe()), scope, [b"x" * 40_000] * 3)
    refused(sent)


def _install_broken_log(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    class Broken(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise error

    log = logging.getLogger(bodylimit.__name__)
    monkeypatch.setattr(log, "propagate", False)
    monkeypatch.setattr(log, "handlers", [Broken()])
    monkeypatch.setattr(log, "filters", [])


@pytest.mark.parametrize("error", [KeyboardInterrupt(), asyncio.CancelledError()])
async def test_a_base_exception_from_the_log_call_still_propagates(
    error: BaseException, monkeypatch: pytest.MonkeyPatch, clock: type[Clock]
) -> None:
    """Only ``Exception`` is swallowed: a cancellation or an interrupt is not the log's to hide."""
    _install_broken_log(monkeypatch, error)
    with pytest.raises(type(error)):
        await run(BodyLimit(Probe()), hostile_scope(), [SECRET_BODY])


async def test_a_swallowed_log_failure_is_counted_and_reported_once_per_interval_without_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], clock: type[Clock]
) -> None:
    _install_broken_log(monkeypatch, RuntimeError("SINKSECRET"))
    app = BodyLimit(Probe())
    for n in range(3):  # three addresses: each is its own log line, so three log calls
        sent, _ = await run(app, hostile_scope(f"203.0.113.{n + 20}"), [SECRET_BODY])
        refused(sent)
    assert bodylimit.log_failures == 3
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "RuntimeError" in err
    assert "1 so far" in err
    for secret in ("SINKSECRET", "203.0.113.9", "/api/auth", "SECRETBODY", "SESSIONSECRET"):
        assert secret not in err
    clock.now += bodylimit.LOG_INTERVAL_SECONDS
    await run(app, hostile_scope("203.0.113.99"), [SECRET_BODY])
    assert "4 so far" in capsys.readouterr().err


async def test_the_table_of_remembered_addresses_is_bounded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    monkeypatch.setattr(bodylimit, "_LOG_TRACKED", 3)
    app = BodyLimit(Probe())
    for n in range(10):
        await run(app, hostile_scope(f"203.0.113.{n}"), [SECRET_BODY])
    assert len(bodylimit._logged) <= 3  # pyright: ignore[reportPrivateUsage]
    assert len(refusal_records(caplog)) == 10


async def test_the_logged_address_is_the_one_the_rest_of_the_app_uses(
    caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    """Behind a trusted proxy the forwarded client; a header from anyone else is not believed."""
    import ipaddress
    from types import SimpleNamespace

    trusted = SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(trusted_proxy_networks=(ipaddress.ip_network("10.0.0.0/8"),))
        )
    )
    app = BodyLimit(Probe())
    await run(app, {**hostile_scope("10.1.2.3", b"198.51.100.7"), "app": trusted}, [SECRET_BODY])
    await run(app, {**hostile_scope("203.0.113.9", b"198.51.100.8"), "app": trusted}, [SECRET_BODY])
    assert [r.client_ip for r in refusal_records(caplog)] == ["198.51.100.7", "203.0.113.9"]  # type: ignore[attr-defined]


async def test_the_real_app_logs_a_refusal_without_the_query_string(
    env: Env, caplog: pytest.LogCaptureFixture, clock: type[Clock]
) -> None:
    caplog.set_level(logging.INFO)
    response = await env.client.post(
        "/api/auth/login?code=OAUTHCODESECRET",
        content=padded(LIMIT + 1),
        headers={"Content-Type": "application/json", "Cookie": "a=SESSIONSECRET"},
    )
    check_refused_response(response)
    (record,) = refusal_records(caplog)
    assert (record.method, record.path, record.status) == ("POST", "/api/auth/login", 413)  # type: ignore[attr-defined]
    assert isinstance(record.client_ip, str)  # type: ignore[attr-defined]
    assert record.client_ip  # type: ignore[attr-defined]
    from classbeat.log import JsonFormatter

    line = JsonFormatter().format(record)
    assert "OAUTHCODESECRET" not in line
    assert "SESSIONSECRET" not in line
