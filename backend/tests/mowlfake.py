"""An in-process fake of the MOWL REST API for the connect tests. Never the network.

It speaks the two sign-in calls ``mowl.api.login`` makes (Authenticate → a login ticket, then the
ticket exchange → the token) and ``POST v1/programs`` (``list_workouts``), on 127.0.0.1 over plain
HTTP; the ``mowl_api`` fixture points ``mowl.api.API`` at it for one test. Public test values only.
"""

import json
import threading
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

import pytest
from mowl import api

EMAIL = "rider@example.com"
PASSWORD = "fake-mowl-password-Zq8"  # a public test value, searched for in every output
TICKET = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TOKEN = "abababab-cdcd-efef-0101-232323232323"
AUTHENTICATE = f"/v1/Authentication/Authenticate/{api.APP_GUID}"
EXCHANGE = f"/v1/Authentication/Ticket/{TICKET}/{api.TICKET_GUID}"


@dataclass
class FakeMowl:
    url: str = ""
    token: str = TOKEN
    # Status to answer per path ("authenticate", "exchange", "programs"); 200 when absent.
    status: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    retry_after: str | None = None
    requests: list[str] = field(default_factory=lambda: list[str]())
    passwords_seen: list[str] = field(default_factory=lambda: list[str]())

    def answer(self, path: str, form: dict[str, str], authorization: str | None) -> tuple[int, Any]:
        if path == AUTHENTICATE:
            self.passwords_seen.append(form.get("Password", ""))
            if (code := self.status.get("authenticate", 200)) != 200:
                return code, {"Error": f"refused {form.get('Password')}"}
            if form.get("Email") != EMAIL or form.get("Password") != PASSWORD:
                # MOWL's own refusal: HTTP 200 with an error, no ticket.
                return 200, {"Data": None, "Error": f"Wrong password {form.get('Password')}"}
            return 200, {"Data": TICKET, "Error": None}
        if path == EXCHANGE:
            if (code := self.status.get("exchange", 200)) != 200:
                return code, {"Error": f"ticket {TICKET} refused"}
            return 200, {"Data": self.token, "Error": None}
        if path == "/v1/programs":
            if authorization != self.token:
                return 401, {"Error": "unauthorized"}
            if (code := self.status.get("programs", 200)) != 200:
                return code, {"Error": "no"}
            return 200, {"Data": [], "Error": None}
        return 404, {"Error": "not found"}


def _handler(fake: FakeMowl) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode() if length else ""
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            path = self.path.split("?")[0]
            fake.requests.append(f"{self.command} {path}")
            status, body = fake.answer(path, form, self.headers.get("authorization"))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status == 429 and fake.retry_after is not None:
                self.send_header("Retry-After", fake.retry_after)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _serve

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


@pytest.fixture
def mowl_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMowl]:
    fake = FakeMowl()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}/"
    monkeypatch.setattr(api, "API", fake.url)
    # No system or environment proxy between the library and the fake.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    monkeypatch.setattr(urllib.request, "_opener", opener)
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
