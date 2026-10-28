"""A fake OpenID Connect provider for the E2E stack. TEST ONLY: it runs under the `e2e` compose
profile (compose.e2e.yaml), never by default, and holds no real credentials.

classbeat's provider endpoints are the real ones (ADR 0003 section 5), so this service answers
as them: the compose network aliases `oauth2.googleapis.com`, `www.googleapis.com` and
`login.microsoftonline.com` point here, and the web container trusts the throwaway CA this
process creates at start (written to /ca/ca.pem). Nothing leaves the stack.

Only the back channel lives here (token exchange and JWKS). The browser's authorization step is
played by Playwright, which answers the redirect to the provider's authorize URL itself and
puts everything the fake needs into the authorization `code`:
base64url(JSON {sub, email, email_verified, nonce, [tid, oid]}). The ID token is built from it.
"""

import base64
import datetime
import json
import os
import ssl
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

HOSTS = ("oauth2.googleapis.com", "www.googleapis.com", "login.microsoftonline.com")
CA_PATH = os.environ.get("FAKE_OIDC_CA_PATH", "/ca/ca.pem")
# Public test values, also set on the web service in compose.e2e.yaml.
CLIENT_SECRET = os.environ.get("FAKE_OIDC_CLIENT_SECRET", "e2e-fake-client-secret")
KID = "fake-1"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def int_b64(value: int) -> str:
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def jwks() -> dict[str, object]:
    numbers = KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": int_b64(numbers.n),
                "e": int_b64(numbers.e),
            }
        ]
    }


def sign(claims: dict[str, object]) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": KID}
    signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
    signature = KEY.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{b64(signature)}"


def id_token(host: str, client_id: str, grant: dict[str, object]) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "aud": client_id,
        "iat": now,
        "exp": now + 600,
        "auth_time": now,
        "nonce": grant["nonce"],
        "email": grant.get("email"),
        "email_verified": grant.get("email_verified", False),
    }
    if host == "login.microsoftonline.com":
        tid, oid = str(grant["tid"]), str(grant["oid"])
        claims.update(iss=f"https://login.microsoftonline.com/{tid}/v2.0", tid=tid, oid=oid, sub=oid)
    else:
        claims.update(iss="https://accounts.google.com", sub=grant["sub"])
    return sign(claims)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: dict[str, object]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def host(self) -> str:
        return (self.headers.get("Host") or "").split(":")[0]

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/oauth2/v3/certs", "/common/discovery/v2.0/keys"):
            self.reply(200, jwks())
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path not in ("/token", "/common/oauth2/v2.0/token"):
            self.reply(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        if form.get("client_secret") != CLIENT_SECRET or not form.get("code_verifier"):
            self.reply(401, {"error": "invalid_client"})
            return
        try:
            grant = json.loads(unb64(form.get("code", "")))
            token = id_token(self.host(), form.get("client_id", ""), grant)
        except (ValueError, KeyError):
            self.reply(400, {"error": "invalid_grant"})
            return
        self.reply(
            200,
            {"access_token": "fake-access-token", "token_type": "Bearer", "expires_in": 3600, "id_token": token},
        )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # no query strings or codes in the logs


def make_certificates(directory: str) -> tuple[str, str]:
    """A throwaway CA and a server certificate for HOSTS. Returns the server cert and key paths;
    the CA certificate (public) is written to CA_PATH for the web container to trust."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "classbeat e2e fake CA")])
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
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in HOSTS]), critical=False)
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
    with open(CA_PATH, "wb") as handle:
        handle.write(ca.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main() -> None:
    port = int(os.environ.get("FAKE_OIDC_PORT", "443"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 - inside the compose network
    with tempfile.TemporaryDirectory() as directory:
        cert, key = make_certificates(directory)  # also writes CA_PATH: the health signal
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
