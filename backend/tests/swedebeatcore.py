"""A fake of Swedebeat's login and catalogue search, for the backend tests and the e2e stack.

TEST ONLY, public test values only. The same module serves both: ``swedebeatfake.py`` runs it in
process over plain HTTP on 127.0.0.1, and ``e2e/fake-swedebeat/server.py`` (mounted read-only)
carries it over TLS as ``www.swedebeat.se`` and ``cloud.swedebeat.se``. It needs a TEST app key
(32 and 16 characters), never Swedebeat's: the tests make one per run, and the e2e stack uses the
public ``e2e/fake-swedebeat/app.json``.

It speaks what ``mowl.swedebeat`` calls:

- ``POST /downloader/login.php``: the body is JSON ``{email, password, deviceid, force}`` whose
  first three fields are the app's encoding (AES-CBC over ``SwdBeat<epoch>:%%:<value>`` with a
  zero pad, hex, Base64). The fake **decodes them with the test key**, so a login only succeeds if
  the caller encoded with that key and that format, and the epoch is near now. A wrong password or
  an unknown email is HTTP 200 with a ``message`` and no token (as the plugin expects); an account
  whose session another device holds refuses with "Device already in use" unless ``force``; the
  outage mode answers 503.
- ``GET /track/extendedsearch``: needs ``X-Auth-Token`` of a token the fake issued (else 401) and
  records which account's token each call carried.

It never records a password or a token: ``logins`` hold the email, the device id and ``force``;
``calls`` hold the path and the email the token belongs to.
"""

import base64
import json
import secrets
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qsl

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

LOGIN_PATH = "/downloader/login.php"
SEARCH_PATH = "/track/extendedsearch"
CONTROL = "/__e2e/"
MAX_CLOCK_SKEW = 300  # seconds between the encoded epoch and the fake's clock
DEVICE_IN_USE = "Device already in use"
WRONG_LOGIN = "Wrong email or password"


@dataclass
class Account:
    password: str
    name: str
    holder: str | None = None  # the device id that holds the session


@dataclass
class FakeSwedebeat:
    key: str
    iv: str
    accounts: dict[str, Account] = field(default_factory=lambda: dict[str, Account]())
    tokens: dict[str, str] = field(default_factory=lambda: dict[str, str]())  # token -> email
    logins: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    calls: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    outage: bool = False
    # Test hook: what the next login with a held session says (to try the other wording).
    in_use_message: str = DEVICE_IN_USE

    def add_account(self, email: str, password: str, name: str = "Rider") -> None:
        self.accounts[email] = Account(password, name)

    def reset(self) -> None:
        self.logins.clear()
        self.calls.clear()
        self.tokens.clear()
        self.outage = False
        for account in self.accounts.values():
            account.holder = None

    def hold(self, email: str, device_id: str) -> None:
        """Another device (the phone app) takes the account's session."""
        self.accounts[email].holder = device_id

    def decode(self, field_value: object) -> str | None:
        """The app's ``encodeString`` reversed with the test key; ``None`` unless it is exactly
        that format and its epoch is near now."""
        try:
            assert isinstance(field_value, str)
            raw = bytes.fromhex(base64.b64decode(field_value, validate=True).decode("ascii"))
            decryptor = Cipher(
                algorithms.AES(self.key.encode("latin-1")), modes.CBC(self.iv.encode("latin-1"))
            ).decryptor()
            plain = (decryptor.update(raw) + decryptor.finalize()).rstrip(b"\x00").decode("utf-8")
            head, separator, value = plain.partition(":%%:")
            assert separator
            assert head.startswith("SwdBeat")
            assert abs(int(head.removeprefix("SwdBeat")) - time.time()) <= MAX_CLOCK_SKEW
        except (AssertionError, ValueError):
            return None
        return value

    def login(self, body: object) -> tuple[int, dict[str, Any]]:
        if self.outage:
            return 503, {"message": "maintenance"}
        if not isinstance(body, dict):
            return 400, {"message": "bad request"}
        fields: dict[str, Any] = body  # pyright: ignore[reportUnknownVariableType]
        email = self.decode(fields.get("email"))
        password = self.decode(fields.get("password"))
        device_id = self.decode(fields.get("deviceid"))
        force = fields.get("force") is True
        if email is None or password is None or not device_id:
            return 400, {"message": "bad request"}
        account = self.accounts.get(email)
        if account is None or account.password != password:
            self.logins.append(
                {"email": email, "device_id": device_id, "force": force, "ok": False}
            )
            return 200, {"message": WRONG_LOGIN}
        if account.holder not in (None, device_id) and not force:
            self.logins.append(
                {"email": email, "device_id": device_id, "force": force, "ok": False}
            )
            return 200, {"message": self.in_use_message}
        account.holder = device_id
        token = "sbt-" + secrets.token_hex(16)
        self.tokens[token] = email
        self.logins.append({"email": email, "device_id": device_id, "force": force, "ok": True})
        return 200, {
            "token": token,
            "customerId": 1000 + len(self.tokens),
            "customerName": account.name,
        }

    def search(self, path: str, token: str | None) -> tuple[int, dict[str, Any]]:
        email = self.tokens.get(token or "")
        self.calls.append({"path": path.split("?")[0], "email": email})
        if email is None:
            return 401, {"message": "unauthorized"}
        return 200, {"data": {"hit": []}}

    def state(self) -> dict[str, Any]:
        return {"logins": list(self.logins), "calls": list(self.calls), "outage": self.outage}

    def control(self, path: str, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        """The e2e stack's test control (``/__e2e/...``): reset, state, hold, outage."""
        name = path.removeprefix(CONTROL)
        if name == "reset":
            self.reset()
        elif name == "state":
            return 200, self.state()
        elif name == "hold" and query.get("email") in self.accounts:
            self.hold(query["email"], query.get("device", "phone-device-0001"))
        elif name == "outage":
            self.outage = query.get("on") == "1"
        else:
            return 404, {"message": "unknown control"}
        return 200, {"ok": True}


def make_handler(fake: FakeSwedebeat) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if self.path.split("?")[0] != LOGIN_PATH:
                self._reply(404, {"message": "not found"})
                return
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            self._reply(*fake.login(body))

        def do_GET(self) -> None:
            path, _, query = self.path.partition("?")
            if path.startswith(CONTROL):
                pairs = dict(parse_qsl(query))
                self._reply(*fake.control(path, pairs))
            elif path == SEARCH_PATH:
                self._reply(*fake.search(self.path, self.headers.get("X-Auth-Token")))
            else:
                self._reply(404, {"message": "not found"})

        def log_message(self, format: str, *args: object) -> None:
            return  # no paths, bodies or headers in any log

    return Handler
