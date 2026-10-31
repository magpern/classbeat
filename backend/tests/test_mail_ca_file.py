"""``smtp_ca_file``: pinning a self-signed SMTP certificate, e.g. Proton Mail Bridge (#372)."""

import datetime
import socket
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from classbeat.config import Settings
from classbeat.mail import Mailer, MailError, MailMessage


def settings(**values: object) -> Settings:
    return Settings(**values)  # pyright: ignore[reportArgumentType]


def self_signed(directory: Path, name: str, host: str = "127.0.0.1") -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.UTC)
    san = x509.DNSName(host)
    if host.replace(".", "").isdigit():
        import ipaddress

        san = x509.IPAddress(ipaddress.ip_address(host))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / f"{name}.pem", directory / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class StarttlsServer:
    """An SMTP server (own thread) that offers STARTTLS with one certificate and takes one
    message. ``delivered`` is set only when a message arrived over the TLS session."""

    def __init__(self, cert: Path, key: Path) -> None:
        self.delivered = threading.Event()
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(cert, key)
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port: int = self._sock.getsockname()[1]
        self._closed = False
        self._open: list[socket.socket] = []
        threading.Thread(target=self._run, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        self._sock.close()

    def _run(self) -> None:
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                self._session(conn)
            except (OSError, ssl.SSLError):
                pass
            finally:
                for sock in self._open:
                    sock.close()
                self._open.clear()
                conn.close()

    def _session(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        stream = conn.makefile("rwb")

        def say(text: str) -> None:
            stream.write(f"{text}\r\n".encode())
            stream.flush()

        say("220 fake ESMTP")
        secure = False
        while line := stream.readline().decode().strip():
            verb = line.split(" ", 1)[0].upper()
            if verb == "EHLO":
                say("250 fake" if secure else "250-fake\r\n250 STARTTLS")
            elif verb == "STARTTLS":
                say("220 go ahead")
                tls = self._context.wrap_socket(conn, server_side=True)
                self._open.append(tls)
                stream = tls.makefile("rwb")
                secure = True
            elif verb == "DATA":
                say("354 go")
                while stream.readline().strip() != b".":
                    pass
                self.delivered.set()
                say("250 queued")
            elif verb == "QUIT":
                say("221 bye")
                return
            else:
                say("250 ok")


def mailer(port: int, ca_file: Path | None) -> Mailer:
    return Mailer(
        settings(smtp_host="127.0.0.1", smtp_port=port, smtp_tls="starttls", smtp_ca_file=ca_file)
    )


MESSAGE = MailMessage("to@example.com", "s", "b")


async def test_the_pinned_self_signed_certificate_is_accepted(tmp_path: Path) -> None:
    cert, key = self_signed(tmp_path, "bridge")
    server = StarttlsServer(cert, key)
    try:
        await mailer(server.port, cert).send(MESSAGE)
        assert server.delivered.is_set()
    finally:
        server.close()


async def test_a_different_certificate_than_the_pinned_one_is_refused(tmp_path: Path) -> None:
    served, key = self_signed(tmp_path, "served")
    pinned, _ = self_signed(tmp_path, "pinned")
    server = StarttlsServer(served, key)
    try:
        with pytest.raises(MailError):
            await mailer(server.port, pinned).send(MESSAGE)
        assert not server.delivered.is_set()
    finally:
        server.close()


async def test_unset_a_self_signed_certificate_is_still_refused(tmp_path: Path) -> None:
    cert, key = self_signed(tmp_path, "bridge")
    server = StarttlsServer(cert, key)
    try:
        with pytest.raises(MailError):
            await mailer(server.port, None).send(MESSAGE)
        assert not server.delivered.is_set()
    finally:
        server.close()


async def test_pinning_does_not_switch_the_hostname_check_off(tmp_path: Path) -> None:
    cert, key = self_signed(tmp_path, "bridge", host="proton-bridge")
    server = StarttlsServer(cert, key)
    try:
        with pytest.raises(MailError):  # the pinned certificate does not name 127.0.0.1
            await mailer(server.port, cert).send(MESSAGE)
        assert not server.delivered.is_set()
    finally:
        server.close()


def test_unset_by_default_and_empty_means_unset() -> None:
    assert settings().smtp_ca_file is None
    assert settings(smtp_ca_file="").smtp_ca_file is None


def test_the_file_must_exist_and_be_a_pem_certificate(tmp_path: Path) -> None:
    cert, key = self_signed(tmp_path, "bridge")
    assert settings(smtp_host="proton-bridge", smtp_ca_file=cert).smtp_ca_file == cert
    empty = tmp_path / "empty.pem"
    empty.write_text("", encoding="utf-8")
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate\n", encoding="utf-8")
    for bad in (tmp_path / "missing.pem", empty, junk, key, tmp_path):
        with pytest.raises(ValidationError) as excinfo:
            settings(smtp_host="proton-bridge", smtp_ca_file=bad)
        assert str(tmp_path) not in str(excinfo.value)


def test_the_file_is_refused_with_plain_smtp(tmp_path: Path) -> None:
    cert, _ = self_signed(tmp_path, "bridge")
    with pytest.raises(ValidationError):
        settings(smtp_host="mailpit", smtp_tls="none", smtp_ca_file=cert)
    assert settings(smtp_host="proton-bridge", smtp_tls="tls", smtp_ca_file=cert)


def test_the_file_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cert, _ = self_signed(tmp_path, "bridge")
    monkeypatch.setenv("CLASSBEAT_SMTP_CA_FILE", str(cert))
    assert Settings().smtp_ca_file == cert
