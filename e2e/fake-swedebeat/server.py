"""A fake Swedebeat for the E2E stack (ADR 0015). TEST ONLY: it runs under the `e2e` compose
profile (compose.e2e.yaml), never by default, and holds no real account, key or credential.

classbeat reaches Swedebeat at its real hosts (the `mowl-workouts` library's `WWW` and `CLOUD`
constants), so this service answers as both: the compose network aliases `www.swedebeat.se` and
`cloud.swedebeat.se` point here, and web trusts a bundle of the other fakes' throwaway CAs plus
this process's (written to /ca/bundle.pem at start, the health signal). It publishes no port.

The fake itself is `backend/tests/swedebeatcore.py`, mounted at /fake-core: the same code the
backend tests run in-process, so the two cannot drift. It decodes the library's AES-encoded login
fields with the TEST app key of `app.json` (a public test value, also mounted into web as its
`swedebeat_app` secret; never Swedebeat's key), so a login only succeeds if classbeat encoded
correctly. It never logs a request: no password, token or device id reaches its output.

Public test data (also in e2e/tests/swedebeat-helpers.ts): the accounts EMAIL_A with PASSWORD_A
and EMAIL_B with PASSWORD_B. `/__e2e/...` is the test control, reached from the compose network
only (tests call it with `docker compose exec web`): `reset`, `state` (every login with email,
device id, force and outcome; every catalogue call with the email its token belongs to; never a
token), `hold?email=&device=` (another device, the phone app, takes that account's session) and
`outage?on=1|0`.
"""

import datetime
import json
import os
import ssl
import sys
import tempfile
from http.server import ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.environ.get("FAKE_SWEDEBEAT_CORE", "/fake-core"))
import swedebeatcore  # noqa: E402 - from the mounted directory above

HOSTS = ("www.swedebeat.se", "cloud.swedebeat.se")
PREVIOUS_CA = os.environ.get("FAKE_SWEDEBEAT_PREVIOUS_CA", "/spotify-ca/bundle.pem")
BUNDLE = os.environ.get("FAKE_SWEDEBEAT_BUNDLE", "/ca/bundle.pem")
APP_FILE = os.environ.get("FAKE_SWEDEBEAT_APP", "/fake/app.json")
ACCOUNTS = (
    ("rider@example.test", "e2e-fake-swedebeat-password", "E2E Rider One"),
    ("second@example.test", "e2e-fake-swedebeat-second", "E2E Rider Two"),
)


def seeded() -> swedebeatcore.FakeSwedebeat:
    with open(APP_FILE, encoding="utf-8") as handle:
        app = json.load(handle)
    fake = swedebeatcore.FakeSwedebeat(app["key"], app["iv"])
    for email, password, name in ACCOUNTS:
        fake.add_account(email, password, name)
    return fake


def make_certificates(directory: str) -> tuple[str, str]:
    """A throwaway CA and a certificate for both hosts. Writes the bundle (the other fakes' CAs
    plus this CA, all public) and returns the server certificate and key paths."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "classbeat e2e fake Swedebeat CA")])
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
            x509.SubjectAlternativeName([x509.DNSName(host) for host in HOSTS]), critical=False
        )
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
    with open(PREVIOUS_CA, "rb") as handle:
        previous = handle.read()
    partial = BUNDLE + ".tmp"
    with open(partial, "wb") as handle:
        handle.write(previous.rstrip(b"\n") + b"\n" + ca.public_bytes(serialization.Encoding.PEM))
    os.replace(partial, BUNDLE)  # appears complete or not at all
    return cert_path, key_path


def main() -> None:
    port = int(os.environ.get("FAKE_SWEDEBEAT_PORT", "443"))
    fake = seeded()
    server = ThreadingHTTPServer(  # noqa: S104 - inside the compose network
        ("0.0.0.0", port), swedebeatcore.make_handler(fake)
    )
    with tempfile.TemporaryDirectory() as directory:
        cert, key = make_certificates(directory)  # also writes BUNDLE: the health signal
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
