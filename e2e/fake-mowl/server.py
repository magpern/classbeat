"""A fake MOWL API for the E2E stack. TEST ONLY: it runs under the `e2e` compose profile
(compose.e2e.yaml), never by default, and holds no real account or credential.

classbeat's MOWL client is the `mowl-workouts` library, whose base URL `https://api.mowl.com/` is
hard-coded, so this service answers as that host: the compose network alias `api.mowl.com`
points here and the web container trusts a bundle of the fake OIDC provider's CA and this
process's throwaway CA (written to /ca/bundle.pem at start, the health signal). Nothing leaves
the stack.

It speaks the sign-in (`login` in mowl/api.py) and the Spotify playlist list (`spotify_playlists`,
for the new-class form, U1): Authenticate (form Email, Password) gives a
login ticket, and the ticket exchange gives the token. The account is a public test value:
EMAIL with PASSWORD. Any other pair gets MOWL's own refusal (HTTP 200 with an `Error`). It never
logs a request: no password, ticket or token reaches its output.
"""

import datetime
import json
import os
import ssl
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

HOST = "api.mowl.com"
# The constant app ids of mowl/api.py (APP_GUID, TICKET_GUID): part of the sign-in paths.
APP_GUID = "C00869F2-4457-486D-ADAD-47409605B187"
TICKET_GUID = "8285D895-29F6-425B-AE60-1725A68FF42E"
# Public test values, also used by e2e/tests/mowl.spec.ts.
EMAIL = os.environ.get("FAKE_MOWL_EMAIL", "rider@example.test")
PASSWORD = os.environ.get("FAKE_MOWL_PASSWORD", "e2e-fake-mowl-password")
OIDC_CA = os.environ.get("FAKE_MOWL_OIDC_CA", "/oidc-ca/ca.pem")
BUNDLE = os.environ.get("FAKE_MOWL_BUNDLE", "/ca/bundle.pem")

TICKETS: set[str] = set()
# Spotify playlists as MOWL lists them (22-character Spotify ids): public test data.
PLAYLISTS = [
    {"ID": "0aBcDeFgHiJkLmNoPqRsTu", "Name": "E2E Hill Climb", "TrackCount": 12, "HasUnavailableTracks": False},
    {"ID": "1zYxWvUtSrQpOnMlKjIhGf", "Name": "E2E Sprints", "TrackCount": 9, "HasUnavailableTracks": False},
]


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: dict[str, object]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path == "/v1/Spotify/Playlists":
            # The new-class form's playlist list (U1): public test data, any caller.
            self.reply(200, {"Data": PLAYLISTS, "Error": None})
            return
        if urlsplit(self.path).path != f"/v1/Authentication/Authenticate/{APP_GUID}":
            self.reply(404, {"Error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        if form.get("Email", "").lower() != EMAIL or form.get("Password") != PASSWORD:
            self.reply(200, {"Data": None, "Error": "Invalid email or password"})
            return
        ticket = str(uuid.uuid4())
        TICKETS.add(ticket)
        self.reply(200, {"Data": ticket, "Error": None})

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path).path.split("/")
        # /v1/Authentication/Ticket/<ticket>/<TICKET_GUID>
        if len(parts) == 6 and parts[1:4] == ["v1", "Authentication", "Ticket"]:
            if parts[5] == TICKET_GUID and parts[4] in TICKETS:
                TICKETS.discard(parts[4])  # single use
                self.reply(200, {"Data": str(uuid.uuid4()), "Error": None})
                return
            self.reply(401, {"Error": "unknown ticket"})
            return
        self.reply(404, {"Error": "not found"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # no paths (they hold tickets) or bodies in the logs


def make_certificates(directory: str) -> tuple[str, str]:
    """A throwaway CA and a certificate for HOST. Writes the bundle (the OIDC fake's CA plus this
    CA, both public) and returns the server certificate and key paths."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "classbeat e2e fake MOWL CA")])
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
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = os.path.join(directory, "server.pem"), os.path.join(directory, "server.key")
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
    with open(OIDC_CA, "rb") as handle:
        oidc_ca = handle.read()
    partial = BUNDLE + ".tmp"
    with open(partial, "wb") as handle:
        handle.write(oidc_ca.rstrip(b"\n") + b"\n" + ca.public_bytes(serialization.Encoding.PEM))
    os.replace(partial, BUNDLE)  # appears complete or not at all
    return cert_path, key_path


def main() -> None:
    port = int(os.environ.get("FAKE_MOWL_PORT", "443"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - inside the compose network
    with tempfile.TemporaryDirectory() as directory:
        cert, key = make_certificates(directory)  # also writes BUNDLE: the health signal
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
