"""A fake of the SwedeBeat gateway (ADR 0035 section 9): ``/auth/login``, ``/auth/session`` and
``/catalog/match`` of the contract (``docs/api.md`` of ``magpern/sb-gateway-service``), for the
backend tests and the e2e stack.

TEST ONLY, public test values only, no network of its own. The same module serves both:
``gatewayfake.py`` runs it in process over an httpx transport, and ``e2e/fake-gateway/server.py``
(mounted read-only) carries it over TLS as ``gateway.classbeat-e2e.test``. It knows no SwedeBeat
secret and talks to nothing.

It keeps the contract's order of checks: the body size (``413`` over 2 MB), then ``X-API-Key``
(``401 {"detail": "unknown client"}``), then the bearer on every route but the login (``401
"missing, invalid or expired bearer; POST /auth/login"``), then the schema (``422`` with a
**list** ``detail``), then the work. A refused login is ``401`` with a relayed message. Errors
are ``{"detail": <string>}`` otherwise.

- **Login** issues an opaque bearer per login (two users get two bearers) whose expiry the test
  can move (``advance``, ``expire``); unknown fields (``force``) are ignored, as pydantic does.
- **Match** answers ``tracks`` mode only, one item per track in order, from ``catalogue``
  (``(title, artist)`` folded, to ``found`` or ``unsure``; anything else is ``missing``).
  ``playlist`` mode is a ``422`` here: classbeat never sends it (ADR 0035 section 7).
- **The record** (``requests``) holds every request's method, path, headers (the key and the
  bearer too, so a test can check exactly what was sent), body and the account its bearer
  belongs to. ``public_state`` is the same without any header value, password or bearer, for
  the e2e control.
- **Injections** (``inject``) replace the next answer of a path, in the contract's place:
  ``unknown_client`` at the key check, ``bearer_401`` at the bearer check, and after them
  ``status`` (any status, with ``detail``), ``rate_limited`` (``429`` with ``Retry-After``),
  ``slow`` (the answer starts after ``seconds``), ``trickle`` (its body one byte per
  ``seconds``), ``oversize`` (a valid answer padded to ``size`` bytes), ``gzip`` (the answer
  gzipped), ``redirect`` (``3xx`` to ``location``), ``wrong_count`` (one item too few),
  ``all_missing``, ``body`` (a raw ``200`` answer of the test's own) and ``raw`` (a raw answer
  with ``status``).
"""

import gzip
import json
import secrets
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qsl

LOGIN_PATH = "/auth/login"
SESSION_PATH = "/auth/session"
MATCH_PATH = "/catalog/match"
HEALTH_PATH = "/health"
CONTROL = "/__e2e/"
BODY_MAX = 2 * 1024 * 1024
UNKNOWN_CLIENT = "unknown client"
BEARER_REFUSED = "missing, invalid or expired bearer; POST /auth/login"
WRONG_LOGIN = "Wrong email or password"
TTL_SECONDS = 86400
FOUND_SCORE = 0.93
UNSURE_SCORE = 0.61
UNSURE_TITLE = "A Different Tune Entirely"
SECONDS = 240  # every catalogue track's length
_JSON = {"content-type": "application/json"}


@dataclass
class Reply:
    """One answer: ``delay`` seconds before it starts, ``trickle`` seconds between its bytes."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=lambda: dict(_JSON))
    delay: float = 0.0
    trickle: float = 0.0


@dataclass
class Session:
    email: str
    expires_at: float
    customer_id: str


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]  # lower-case names
    body: Any  # the parsed JSON, or None
    email: str | None  # the account of the bearer it carried, if any


@dataclass
class Injection:
    kind: str
    path: str | None  # None: the next request of any path
    status: int = 502
    seconds: float = 0.0
    retry_after: str = "30"
    location: str = "/elsewhere"
    size: int = 0
    body: bytes = b""


def _reply(status: int, payload: object, headers: dict[str, str] | None = None) -> Reply:
    return Reply(status, json.dumps(payload).encode(), {**_JSON, **(headers or {})})


def _schema_error(loc: str) -> Reply:
    # The schema 422: a list detail that never echoes the submitted values.
    return _reply(422, {"detail": [{"type": "value_error", "loc": ["body", loc], "msg": "bad"}]})


def _text(value: object, low: int, high: int) -> bool:
    return isinstance(value, str) and low <= len(value) <= high


@dataclass
class FakeGateway:
    client_keys: set[str]
    users: dict[str, str] = field(default_factory=lambda: dict[str, str]())  # email -> password
    bearers: dict[str, Session] = field(default_factory=lambda: dict[str, Session]())
    catalogue: dict[tuple[str, str], str] = field(
        default_factory=lambda: dict[tuple[str, str], str]()
    )
    requests: list[Recorded] = field(default_factory=lambda: list[Recorded]())
    injections: list[Injection] = field(default_factory=lambda: list[Injection]())
    ttl: int = TTL_SECONDS
    offset: float = 0.0  # added to the clock: a test moves time with ``advance``

    def add_user(self, email: str, password: str) -> None:
        self.users[email] = password

    def now(self) -> float:
        return time.time() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds

    def expire(self, bearer: str) -> None:
        self.bearers[bearer].expires_at = self.now() - 1

    def add_track(self, title: str, artist: str, status: str) -> None:
        """``status`` is ``found`` or ``unsure``; any other song is ``missing``."""
        self.catalogue[(title.strip().casefold(), artist.strip().casefold())] = status

    def inject(self, kind: str, path: str | None = None, **params: Any) -> None:
        self.injections.append(Injection(kind, path, **params))

    def reset(self) -> None:
        self.bearers.clear()
        self.catalogue.clear()
        self.requests.clear()
        self.injections.clear()
        self.offset = 0.0

    def _take(self, path: str, kinds: tuple[str, ...]) -> Injection | None:
        for injection in self.injections:
            if injection.kind in kinds and injection.path in (None, path):
                self.injections.remove(injection)
                return injection
        return None

    def handle(self, method: str, path: str, headers: dict[str, str], raw: bytes) -> Reply:
        """The answer to one request (headers with lower-case names)."""
        route = path.split("?", 1)[0]
        if route == HEALTH_PATH:
            return _reply(200, {"status": "ok"})
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = None
        bearer = headers.get("authorization", "").removeprefix("Bearer ")
        session = self.bearers.get(bearer)
        self.requests.append(
            Recorded(method, route, dict(headers), body, session.email if session else None)
        )
        if len(raw) > BODY_MAX:
            return _reply(413, {"detail": "request body too large"})
        if headers.get("x-api-key") not in self.client_keys or self._take(
            route, ("unknown_client",)
        ):
            return _reply(401, {"detail": UNKNOWN_CLIENT})
        if route != LOGIN_PATH:
            valid = session is not None and session.expires_at > self.now()
            if not valid or self._take(route, ("bearer_401",)):
                return _reply(401, {"detail": BEARER_REFUSED})
        if (route, method) == (LOGIN_PATH, "POST"):
            answer = self._login(body)
        elif (route, method) == (SESSION_PATH, "GET"):
            assert session is not None
            answer = _reply(200, {"valid": True, "customer_id": session.customer_id})
        elif (route, method) == (MATCH_PATH, "POST"):
            answer = self._match(body, route)
        else:
            return _reply(404, {"detail": "Not Found"})
        return self._injected(route, answer)

    def _login(self, body: Any) -> Reply:
        if not isinstance(body, dict):
            return _schema_error("body")
        fields: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        email, password = fields.get("email"), fields.get("password")
        device_id = fields.get("device_id")
        if not isinstance(email, str) or not _text(email, 3, 254):
            return _schema_error("email")
        if not isinstance(password, str) or not _text(password, 1, 256):
            return _schema_error("password")
        if device_id is not None and not _text(device_id, 0, 64):
            return _schema_error("device_id")
        if self.users.get(email) != password:
            return _reply(401, {"detail": WRONG_LOGIN})
        bearer = "gAAAAB" + secrets.token_urlsafe(48)
        customer = str(35900 + len(self.bearers))
        self.bearers[bearer] = Session(email, self.now() + self.ttl, customer)
        payload = {"bearer": bearer, "token_type": "Bearer", "expires_in": self.ttl}
        return _reply(200, {**payload, "customer_id": customer})

    def _item(self, track: dict[str, str]) -> dict[str, Any]:
        title, artist = track["title"], track["artist"]
        status = self.catalogue.get((title.strip().casefold(), artist.strip().casefold()))
        if status is None:
            return {
                "title": title,
                "artist": artist,
                "status": "missing",
                "score": 0.0,
                "track": None,
            }
        hit = title if status == "found" else UNSURE_TITLE
        score = FOUND_SCORE if status == "found" else UNSURE_SCORE
        found = {"reference": "ref-" + secrets.token_hex(4), "title": hit, "artist": artist}
        found |= {"bpm": 128.0, "duration": float(SECONDS)}
        return {"title": title, "artist": artist, "status": status, "score": score, "track": found}

    def _match(self, body: Any, route: str) -> Reply:
        if not isinstance(body, dict):
            return _schema_error("body")
        fields: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        if "playlist" in fields:
            return _reply(422, {"detail": "this fake answers the tracks mode only"})
        tracks = fields.get("tracks")
        if not isinstance(tracks, list) or len(tracks) > 200:  # pyright: ignore[reportUnknownArgumentType]
            return _schema_error("tracks")
        for track in tracks:  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(track, dict) or not all(
                _text(track.get(k), 1, 300)  # pyright: ignore[reportUnknownMemberType]
                for k in ("title", "artist")
            ):
                return _schema_error("tracks")
        items = [self._item(t) for t in tracks]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        if self._take(route, ("all_missing",)):
            items = [{**i, "status": "missing", "score": 0.0, "track": None} for i in items]
        if self._take(route, ("wrong_count",)):
            items = items[:-1] if items else [self._item({"title": "x", "artist": "y"})]
        counts = {s: sum(i["status"] == s for i in items) for s in ("found", "unsure", "missing")}
        selected = [i["track"] for i in items if i["status"] == "found"]
        answer = {"playlist_name": None, **counts, "items": items, "selected": selected}
        return _reply(200, answer)

    def _injected(self, route: str, answer: Reply) -> Reply:
        kinds = ("status", "rate_limited", "slow", "trickle", "oversize", "gzip", "redirect")
        kinds += ("body", "raw")
        injection = self._take(route, kinds)
        if injection is None:
            return answer
        kind = injection.kind
        if kind == "status":
            return _reply(injection.status, {"detail": "injected"})
        if kind == "rate_limited":
            return _reply(429, {"detail": "slow down"}, {"retry-after": injection.retry_after})
        if kind == "redirect":
            return _reply(injection.status, {}, {"location": injection.location})
        if kind in ("body", "raw"):
            return Reply(200 if kind == "body" else injection.status, injection.body)
        if kind == "slow":
            answer.delay = injection.seconds
        elif kind == "trickle":
            answer.trickle = injection.seconds
        elif kind == "oversize":
            padded = json.loads(answer.body) | {"pad": "x" * injection.size}
            answer.body = json.dumps(padded).encode()
        elif kind == "gzip":
            answer.body = gzip.compress(answer.body)
            answer.headers["content-encoding"] = "gzip"
        return answer

    def public_state(self) -> dict[str, Any]:
        """The record without a header value, a password, a key or a bearer (e2e control)."""
        calls: list[dict[str, Any]] = []
        for r in self.requests:
            body: Any = r.body
            if r.path == LOGIN_PATH and isinstance(body, dict):
                kept: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
                body = {k: v for k, v in kept.items() if k != "password"}
            calls.append(
                {"method": r.method, "path": r.path, "headers": sorted(r.headers), "body": body}
                | {"email": r.email}
            )
        return {"requests": calls}

    def control(self, path: str, query: dict[str, str]) -> Reply:
        """The e2e stack's control (``/__e2e/...``): reset, state, catalogue, inject."""
        name = path.removeprefix(CONTROL)
        if name == "reset":
            self.reset()
        elif name == "state":
            return _reply(200, self.public_state())
        elif name == "catalogue":
            self.catalogue.clear()
            for status in ("found", "unsure"):
                for pair in filter(None, query.get(status, "").split("|")):
                    title, _, artist = pair.partition("~")
                    self.add_track(title, artist or "The Invented Band", status)
        elif name == "inject" and query.get("kind"):
            numbers = {k: float(query[k]) for k in ("seconds",) if k in query}
            if "status" in query:
                numbers["status"] = int(query["status"])
            self.inject(query["kind"], query.get("path"), **numbers)
        else:
            return _reply(404, {"detail": "unknown control"})
        return _reply(200, {"ok": True})


def make_handler(fake: FakeGateway) -> type[BaseHTTPRequestHandler]:
    """An ``http.server`` handler over ``fake`` (the e2e server's)."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, reply: Reply) -> None:
            time.sleep(reply.delay)
            self.send_response(reply.status)
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            if reply.trickle:
                for byte in reply.body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(reply.trickle)
            else:
                self.wfile.write(reply.body)

        def _serve(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(length, BODY_MAX + 1)) if length else b""
            path, _, query = self.path.partition("?")
            if path.startswith(CONTROL):
                self._send(fake.control(path, dict(parse_qsl(query))))
                return
            headers = {k.lower(): v for k, v in self.headers.items()}
            self._send(fake.handle(method, self.path, headers, raw))

        def do_GET(self) -> None:
            self._serve("GET")

        def do_POST(self) -> None:
            self._serve("POST")

        def log_message(self, format: str, *args: object) -> None:
            return  # no path, body or header in any log

    return Handler
