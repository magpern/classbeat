"""A fake SwedeBeat gateway for the E2E stack (ADR 0035 section 9). TEST ONLY: it runs under the
`e2e` compose profile (compose.e2e.yaml), never by default, and holds no real account, key or
credential. It contacts nothing.

classbeat reaches the gateway at the origin `CLASSBEAT_SWEDEBEAT_GATEWAY_URL` names; the stack sets
it to `https://gateway.classbeat-e2e.test`, a compose network alias that points here. Web and the
worker trust a bundle of the other fakes' throwaway CAs plus this process's (written to
/ca/bundle.pem at start, the health signal). It publishes no port.

The fake itself is `backend/tests/gatewaycore.py`, mounted at /fake-core: the same code the
backend tests run in-process, so the two cannot drift. Its one client key is the PUBLIC TEST value
of `client-key.txt` (also mounted into web and the worker as their `swedebeat_gateway_key`
secret; never the owner's key). It never logs a request: no password, key or bearer reaches its
output.

Public test data: the accounts in ACCOUNTS. `/__e2e/...` is the test control, reached from the
compose network only: `reset`, `state` (every request without a header value, password, key or
bearer), `catalogue?found=Title~Artist|...&unsure=...` and `inject?kind=...&path=...`.
"""

import datetime
import os
import ssl
import sys
import tempfile
from http.server import ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.environ.get("FAKE_GATEWAY_CORE", "/fake-core"))
import gatewaycore  # from the mounted directory above

HOST = "gateway.classbeat-e2e.test"
PREVIOUS_CA = os.environ.get("FAKE_GATEWAY_PREVIOUS_CA", "/swedebeat-ca/bundle.pem")
BUNDLE = os.environ.get("FAKE_GATEWAY_BUNDLE", "/ca/bundle.pem")
KEY_FILE = os.environ.get("FAKE_GATEWAY_CLIENT_KEY", "/fake/client-key.txt")
ACCOUNTS = (
    ("rider@example.test", "e2e-fake-gateway-password"),
    ("second@example.test", "e2e-fake-gateway-second"),
)


def seeded() -> gatewaycore.FakeGateway:
    with open(KEY_FILE, encoding="utf-8") as handle:
        key = handle.read().strip()
    fake = gatewaycore.FakeGateway({key})
    for email, password in ACCOUNTS:
        fake.add_user(email, password)
    return fake


def make_certificates(directory: str) -> tuple[str, str]:
    """A throwaway CA and a certificate for HOST. Writes the bundle (the other fakes' CAs plus
    this CA, all public) and returns the server certificate and key paths."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "classbeat e2e fake gateway CA")])
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
    with open(PREVIOUS_CA, "rb") as handle:
        previous = handle.read()
    partial = BUNDLE + ".tmp"
    with open(partial, "wb") as handle:
        handle.write(previous.rstrip(b"\n") + b"\n" + ca.public_bytes(serialization.Encoding.PEM))
    os.replace(partial, BUNDLE)  # appears complete or not at all
    return cert_path, key_path


def main() -> None:
    port = int(os.environ.get("FAKE_GATEWAY_PORT", "443"))
    fake = seeded()
    server = ThreadingHTTPServer(
        ("0.0.0.0", port),  # noqa: S104 - inside the compose network, no published port
        gatewaycore.make_handler(fake),
    )
    with tempfile.TemporaryDirectory() as directory:
        cert, key = make_certificates(directory)  # also writes BUNDLE: the health signal
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
