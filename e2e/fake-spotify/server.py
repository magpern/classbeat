"""A fake Spotify for the E2E stack (ADR 0009 §13). TEST ONLY: it runs under the `e2e` compose
profile (compose.e2e.yaml), never by default, and holds no real app, account or credential.

classbeat calls Spotify at its real hosts (the library's base URL, the token endpoint of
`classbeat.spotify.http`), so this service answers as both: the compose network aliases
`api.spotify.com` and `accounts.spotify.com` point here, and web and worker trust a bundle of the
other fakes' throwaway CAs plus this process's (written to /ca/bundle.pem at start, the health
signal). It publishes no port: nothing outside the compose network reaches it, and nothing leaves
the stack.

The fake itself is `backend/tests/spotifycore.py`, mounted at /fake-core: the same code the
backend tests run in-process, so the two cannot drift. This file only carries HTTP: a `refuse`
injection closes the connection without an answer and applies nothing, a `drop` applies the
request and then closes without an answer, a `trickle` sends the body one byte at a time. It never
logs a request: no code, verifier, state or token reaches its output.

Public test data (also in e2e/tests/spotify-helpers.ts): apps CLIENT_A (allows USER_A) and
CLIENT_B (allows USER_B), both with the stack's callback (`FAKE_SPOTIFY_REDIRECT_URI`), and
USER_A signed in at the authorize page.

`/__e2e/...` is the test control, on either host, reached from the compose network only (the
tests call it with `docker compose exec web`). POST: `reset`; `sign-in?user=<id>` (empty: nobody);
`deny`; `user?id=&name=`; `app?client_id=&allow=a,b&premium=1&rotate=1&scope=`;
`inject?kind=&at=&method=&path=&times=&status=&retry_after=&keep=&delay=&message=` (the kinds of
spotifycore.KINDS); `shape?items=item|track&foreign=404|visible`; `playlist?owner=&name=&tracks=<n>`
(its id back). GET: `state` (every request's number, method, host, path, client id, the app and
user of its token, whether it carried a client secret, its outcome; the playlists; the injections
left; never a token, code or verifier).
"""

import contextlib
import datetime
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.environ.get("FAKE_SPOTIFY_CORE", "/fake-core"))
import spotifycore  # noqa: E402 - from the mounted directory above

HOSTS = (spotifycore.API_HOST, spotifycore.ACCOUNTS_HOST)
CLIENT_A = "e2e0" * 8  # 32 hex characters, public test values
CLIENT_B = "e2e1" * 8
USER_A = "e2e-rider-one"
USER_B = "e2e-rider-two"
REDIRECT_URI = os.environ.get(
    "FAKE_SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/oauth/spotify/callback"
)
OTHER_BUNDLE = os.environ.get("FAKE_SPOTIFY_OTHER_BUNDLE", "/mowl-ca/bundle.pem")
BUNDLE = os.environ.get("FAKE_SPOTIFY_BUNDLE", "/ca/bundle.pem")
CONTROL = "/__e2e/"
STATE_LOCK = threading.Lock()


def seeded() -> spotifycore.FakeSpotify:
    fake = spotifycore.FakeSpotify()
    fake.add_user(USER_A, "E2E Rider One")
    fake.add_user(USER_B, "E2E Rider Two")
    fake.add_app(CLIENT_A, redirect_uris=[REDIRECT_URI], allowed=[USER_A])
    fake.add_app(CLIENT_B, redirect_uris=[REDIRECT_URI], allowed=[USER_B])
    fake.sign_in(USER_A)
    return fake


FAKE = {"now": seeded()}


def fake() -> spotifycore.FakeSpotify:
    with STATE_LOCK:
        return FAKE["now"]


def _flag(query: dict[str, str], name: str, default: bool) -> bool:
    return query[name] not in ("0", "false", "") if name in query else default


def control(method: str, action: str, query: dict[str, str]) -> tuple[int, object]:
    """One test-control call: (status, JSON body)."""
    current = fake()
    if method == "GET" and action == "state":
        return 200, current.summary()
    if method != "POST":
        return 405, {"error": "POST"}
    if action == "reset":
        with STATE_LOCK:
            FAKE["now"] = seeded()
        return 200, {"ok": True}
    if action == "sign-in":
        current.sign_in(query.get("user") or None)
    elif action == "deny":
        current.deny()
    elif action == "user":
        current.add_user(query["id"], query.get("name"))
    elif action == "app":
        current.add_app(
            query["client_id"],
            redirect_uris=[REDIRECT_URI],
            allowed=[u for u in query.get("allow", "").split(",") if u],
            owner_premium=_flag(query, "premium", True),
            rotate_refresh=_flag(query, "rotate", True),
            grant_scope=query.get("scope"),
        )
    elif action == "inject":
        options: dict[str, object] = {}
        for name in ("at", "times", "status", "retry_after", "keep"):
            if name in query:
                options[name] = int(query[name])
        for name in ("method", "path", "message"):
            if name in query:
                options[name] = query[name]
        if "delay" in query:
            options["delay"] = float(query["delay"])
        current.inject(query["kind"], **options)
    elif action == "shape":
        current.item_shape = query.get("items", current.item_shape)
        current.foreign = query.get("foreign", current.foreign)
    elif action == "playlist":
        count = int(query.get("tracks", "0"))
        uris = [f"spotify:track:{spotifycore.track_id(i)}" for i in range(count)]
        playlist = current.add_playlist(query["owner"], query.get("name", "E2E"), uris)
        return 200, {"id": playlist.id}
    else:
        return 404, {"error": "unknown control"}
    return 200, {"ok": True}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _host(self) -> str:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host if host in HOSTS else spotifycore.API_HOST

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _send(self, status: int, body: bytes, headers: dict[str, str], trickle: float) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not trickle:
            self.wfile.write(body)
            return
        for i in range(len(body)):
            self.wfile.write(body[i : i + 1])
            self.wfile.flush()
            time.sleep(trickle)

    def _cut(self) -> None:
        """Close the connection without an answer."""
        self.close_connection = True
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def _serve(self) -> None:
        parts = urlsplit(self.path)
        body = self._body()
        if parts.path.startswith(CONTROL):
            query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
            try:
                status, data = control(self.command, parts.path.removeprefix(CONTROL), query)
            except (KeyError, ValueError) as error:
                status, data = 400, {"error": type(error).__name__}
            raw = json.dumps(data).encode()
            self._send(status, raw, {"Content-Type": "application/json"}, 0.0)
            return
        url = f"https://{self._host()}{self.path}"
        answer = fake().handle(self.command, url, dict(self.headers.items()), body)
        if answer.refuse or answer.drop:
            self._cut()
            return
        self._send(answer.status, answer.body, answer.headers, answer.trickle)

    do_GET = do_POST = do_PUT = do_DELETE = _serve  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # no paths (codes, states) or bodies in the logs


def make_certificates(directory: str) -> tuple[str, str]:
    """A throwaway CA and one certificate for both hosts. Writes the bundle (the other fakes'
    bundle plus this CA, all public) and returns the server certificate and key paths."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "classbeat e2e fake Spotify CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOSTS[0])]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = os.path.join(directory, "server.pem")
    key_path = os.path.join(directory, "server.key")
    with open(cert_path, "wb") as handle:
        handle.write(server.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        handle.write(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
    with open(OTHER_BUNDLE, "rb") as handle:
        others = handle.read()
    partial = BUNDLE + ".tmp"
    with open(partial, "wb") as handle:
        handle.write(others.rstrip(b"\n") + b"\n" + ca.public_bytes(serialization.Encoding.PEM))
    os.replace(partial, BUNDLE)  # appears complete or not at all
    return cert_path, key_path


def main() -> None:
    port = int(os.environ.get("FAKE_SPOTIFY_PORT", "443"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - inside the compose network
    server.daemon_threads = True
    with tempfile.TemporaryDirectory() as directory:
        cert, key = make_certificates(directory)  # also writes BUNDLE: the health signal
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
