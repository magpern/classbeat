"""A fake MOWL with several accounts, for the two-user isolation test (issue #67). Never the
network: 127.0.0.1 over plain HTTP, ``mowl.api.API`` pointed at it for one test.

It speaks the same three calls as ``mowlfake`` (Authenticate, ticket exchange, ``POST
v1/programs``) but keeps one account per user and records, per call, *whose* credential the call
carried: the e-mail of an Authenticate, the ticket of an exchange, the token of a programs call.
``programs`` answers with a workout named after the account the token belongs to, so a token that
crossed users also shows up in what the other user sees. Public test values only.

``swap_tokens`` is a deliberately broken double (the exchange hands out the *other* account's
token). It exists only so the test can prove that ``verify`` notices a crossing; nothing outside
the tests has such a switch.
"""

import json
import random
import threading
import time
import urllib.request
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

import pytest
from mowl import api


@dataclass(frozen=True)
class Account:
    label: str
    email: str
    password: str
    ticket: str
    token: str

    @property
    def secrets(self) -> tuple[str, str, str]:
        return (self.password, self.ticket, self.token)


ALICE = Account(
    "alice",
    "alice.rider@example.com",
    "alice-fake-mowl-password-Qw3",
    "a11ce000-0000-4000-8000-00000000a001",
    "a11ce111-1111-4111-8111-11111111a002",
)
BOB = Account(
    "bob",
    "bob.rider@example.com",
    "bob-fake-mowl-password-Rt7",
    "b0b00000-0000-4000-8000-00000000b001",
    "b0b00111-1111-4111-8111-11111111b002",
)
ACCOUNTS = (ALICE, BOB)
assert all(len(v) == 36 for a in ACCOUNTS for v in (a.ticket, a.token))  # the library checks this


@dataclass(frozen=True)
class Call:
    kind: str  # "authenticate", "exchange" or "programs"
    owner: str | None  # the account label the carried credential belongs to; None: unknown
    ok: bool


@dataclass
class TwoMowl:
    url: str = ""
    swap_tokens: bool = False  # the deliberately broken double (see the module docstring)
    calls: list[Call] = field(default_factory=lambda: list[Call]())
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _by(self, **where: str | None) -> Account | None:
        ((key, value),) = where.items()
        return next((a for a in ACCOUNTS if getattr(a, key) == value), None)

    def record(self, kind: str, owner: Account | None, ok: bool) -> None:
        with self.lock:
            self.calls.append(Call(kind, owner.label if owner else None, ok))

    def counts(self) -> Counter[tuple[str, str | None, bool]]:
        return Counter((c.kind, c.owner, c.ok) for c in self.calls)

    def answer(self, path: str, form: dict[str, str], authorization: str | None) -> tuple[int, Any]:
        time.sleep(random.uniform(0, 0.01))  # noqa: S311 - interleave the two users' calls differently each run
        parts = path.strip("/").split("/")
        if parts[:3] == ["v1", "Authentication", "Authenticate"]:
            who = self._by(email=form.get("Email"))
            ok = who is not None and form.get("Password") == who.password
            self.record("authenticate", who, ok)
            if who is None or not ok:
                return 200, {"Data": None, "Error": "wrong e-mail or password"}
            return 200, {"Data": who.ticket, "Error": None}
        if parts[:3] == ["v1", "Authentication", "Ticket"]:
            who = self._by(ticket=parts[3])
            self.record("exchange", who, who is not None)
            if who is None:
                return 200, {"Data": None, "Error": "unknown ticket"}
            if self.swap_tokens:
                who = next(a for a in ACCOUNTS if a is not who)
            return 200, {"Data": who.token, "Error": None}
        if parts == ["v1", "programs"]:
            who = self._by(token=authorization)
            self.record("programs", who, who is not None)
            if who is None:
                return 401, {"Error": "unauthorized"}
            return 200, {"Data": [{"ProgramID": 1, "Name": f"{who.label} workout"}], "Error": None}
        return 404, {"Error": "not found"}


def _handler(fake: TwoMowl) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode() if length else ""
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            status, body = fake.answer(
                self.path.split("?")[0], form, self.headers.get("authorization")
            )
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _serve

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


@pytest.fixture
def two_mowl(monkeypatch: pytest.MonkeyPatch) -> Iterator[TwoMowl]:
    fake = TwoMowl()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}/"
    monkeypatch.setattr(api, "API", fake.url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy in between
    monkeypatch.setattr(urllib.request, "_opener", opener)
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
