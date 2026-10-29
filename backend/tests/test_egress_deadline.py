"""The total deadline of a worker request (ADR 0007 section 6, ``classbeat.egress``): a response
that trickles in a byte at a time never outlasts it, although every byte arrives well inside the
socket timeout; and no request starts after the call's deadline, so a save's ``bound_seconds``
holds. Against a TLS server on loopback with a throwaway certificate; never the network.
"""

import contextlib
import datetime
import ipaddress
import json
import socket
import ssl
import threading
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from mowl import api, workout

from classbeat import egress
from classbeat.connections import Credentials
from classbeat.egress import (
    DeadlineHTTPSConnection,
    DeadlineHTTPSHandler,
    RequestDeadline,
    call_deadline,
    call_deadline_until,
    install_worker_allowlist,
    tls_client_context,
)
from savetools import DRAFT, WRITES, spec_of

TOKEN = "aaaaaaaa-0000-1111-2222-333333333333"  # a public test value
DRIP_SECONDS = 0.05


def _certificates(directory: Path) -> tuple[str, str, str]:
    now = datetime.datetime.now(datetime.UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "classbeat test loopback")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path), str(cert_path)


@dataclass
class DripServer:
    """``drip``: answer 200 with a JSON body sent one byte every ``DRIP_SECONDS``; ``fast``: at
    once. Records each request's path and whether the client went away before the end."""

    port: int = 0
    ca: str = ""
    mode: str = "drip"
    paths: list[str] = field(default_factory=lambda: list[str]())
    cut: list[bool] = field(default_factory=lambda: list[bool]())
    pause: float = 0.0  # each fast answer waits this long first
    accepts: int = 0  # connections opened by the client


def _serve(server: DripServer, listener: socket.socket, context: ssl.SSLContext) -> None:
    while True:
        try:
            raw, _ = listener.accept()
        except OSError:
            return
        server.accepts += 1
        threading.Thread(target=_one, args=(server, raw, context), daemon=True).start()


def _one(server: DripServer, raw: socket.socket, context: ssl.SSLContext) -> None:
    try:
        conn = context.wrap_socket(raw, server_side=True)
    except (OSError, ssl.SSLError):
        return
    with conn:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        server.paths.append(lines[0].split(" ")[1])
        length = next(
            (
                int(v)
                for k, _, v in (h.partition(": ") for h in lines[1:])
                if k.lower() == "content-length"
            ),
            0,
        )
        while len(rest) < length:
            rest += conn.recv(4096)
        body = _answer(lines[0])
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        )
        try:
            if server.mode == "drip":
                for i in range(len(body)):
                    conn.sendall(body[i : i + 1])
                    time.sleep(DRIP_SECONDS)
            else:
                time.sleep(server.pause)
                conn.sendall(body)
            server.cut.append(False)
        except OSError:
            server.cut.append(True)


def _answer(request_line: str) -> bytes:
    method, path, _ = request_line.split(" ")
    if method == "PUT" and path.endswith("/v1/programs"):
        data: Any = {"ProgramID": 41}
    elif method == "PUT" and path.endswith("/v1/segments"):
        data = {"SegmentID": 42}
    else:
        data = {}
    return json.dumps({"Data": data, "Error": None}).encode() + b" " * 200


@pytest.fixture
def drip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DripServer]:
    cert, key, ca = _certificates(tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    listener = socket.create_server(("127.0.0.1", 0))
    server = DripServer(port=listener.getsockname()[1], ca=ca)
    threading.Thread(target=_serve, args=(server, listener, server_context), daemon=True).start()
    # The worker's deadline handler against the loopback server (the allowlist itself is
    # tested elsewhere: it would refuse this host and port).
    opener = urllib.request.OpenerDirector()
    for handler in (
        DeadlineHTTPSHandler(context=tls_client_context(cafile=ca)),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    monkeypatch.setattr(urllib.request, "_opener", opener)
    monkeypatch.setattr(api, "API", f"https://127.0.0.1:{server.port}/")
    try:
        yield server
    finally:
        listener.close()


CREDS = Credentials(mowl_token=TOKEN)


def test_a_slow_drip_answer_is_cut_at_the_request_deadline(drip: DripServer) -> None:
    started = time.monotonic()
    with pytest.raises(RequestDeadline):
        api.call(CREDS, "GET", "v1/programs/1", timeout=1)  # each byte comes in 50 ms
    elapsed = time.monotonic() - started
    assert 0.9 <= elapsed < 1.6
    _until(lambda: drip.cut == [True])  # the server saw the connection go


def test_the_call_deadline_cuts_a_request_with_a_longer_timeout(drip: DripServer) -> None:
    started = time.monotonic()
    with call_deadline(0.8), pytest.raises(RequestDeadline):
        api.call(CREDS, "GET", "v1/programs/1")  # the library's own timeout is 30 s
    assert time.monotonic() - started < 1.4


def test_mutation_without_the_deadline_a_drip_outlasts_the_socket_timeout(
    drip: DripServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same drip through urllib's plain handler: a 1 s socket timeout never fires, so the
    request lasts as long as the server likes (here the whole body, over 10 s if not stopped)."""
    plain = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(context=tls_client_context(cafile=drip.ca)),
        urllib.request.HTTPErrorProcessor(),
    ):
        plain.add_handler(handler)
    monkeypatch.setattr(urllib.request, "_opener", plain)
    finished: list[float] = []

    def call() -> None:
        started = time.monotonic()
        with contextlib.suppress(Exception):  # only the time matters here
            api.call(CREDS, "GET", "v1/programs/1", timeout=1)
        finished.append(time.monotonic() - started)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(2.0)
    assert thread.is_alive()
    assert not finished


def test_no_request_starts_after_the_call_deadline(drip: DripServer) -> None:
    """A save whose requests are each fast but whose whole runs past the bound: the request due
    after the deadline is refused before it is sent, so nothing of the call outlives it."""
    drip.mode, drip.pause = "fast", 0.15
    started = time.monotonic()
    with call_deadline(0.5), pytest.raises(RequestDeadline):
        workout.save(spec_of(DRAFT), CREDS, 7)
    assert time.monotonic() - started < 0.9
    assert 1 <= len(drip.paths) < WRITES  # it stopped part-way
    sent = len(drip.paths)
    time.sleep(0.4)
    assert len(drip.paths) == sent  # and nothing more went out
    assert drip.accepts == sent  # not even a connection for the request due after it


def test_a_request_due_after_the_deadline_never_connects(drip: DripServer) -> None:
    drip.mode = "fast"
    with call_deadline(0.2):
        time.sleep(0.3)  # the library working between two requests
        with pytest.raises(RequestDeadline):
            api.call(CREDS, "GET", "v1/programs/1")
    time.sleep(0.2)
    assert (drip.accepts, drip.paths) == (0, [])


def test_a_request_inside_its_deadline_is_untouched(drip: DripServer) -> None:
    drip.mode = "fast"
    with call_deadline(5):
        answer = workout.save(spec_of(DRAFT), CREDS, 7)
    assert answer["program_id"] == 41
    assert len(drip.paths) == WRITES
    _until(lambda: len(drip.cut) == WRITES)
    assert drip.cut == [False] * WRITES
    assert threading.active_count() < 20  # every watchdog ended with its response


def test_the_worker_opener_uses_the_deadline_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = install_worker_allowlist()
    handlers: list[Any] = opener.handlers  # pyright: ignore[reportAttributeAccessIssue]
    https = [h for h in handlers if hasattr(h, "https_open")]
    assert len(https) == 1
    assert isinstance(https[0], DeadlineHTTPSHandler)
    seen: list[Any] = []
    monkeypatch.setattr(
        DeadlineHTTPSHandler, "do_open", lambda self, cls, req, **kw: seen.append(cls)
    )
    https[0].https_open(urllib.request.Request("https://api.mowl.com/v1/programs"))
    assert seen == [DeadlineHTTPSConnection]


def test_the_request_deadline_rules() -> None:
    now = time.monotonic()
    assert egress.request_deadline(30) == pytest.approx(now + 30, abs=0.5)
    for unset in (None, object(), True, 0, -1):
        assert egress.request_deadline(unset) == pytest.approx(
            now + egress.REQUEST_FALLBACK_SECONDS, abs=0.5
        )
    with call_deadline(5):
        assert egress.request_deadline(30) == pytest.approx(now + 5, abs=0.5)
        with call_deadline(60):  # a nested block keeps the earlier deadline
            assert egress.request_deadline(30) == pytest.approx(now + 5, abs=0.5)
        assert egress.request_deadline(2) == pytest.approx(now + 2, abs=0.5)
    assert egress.request_deadline(30) == pytest.approx(now + 30, abs=0.5)
    with pytest.raises(ValueError, match="positive"), call_deadline(0):
        pass


def _until(check: Any, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "condition never held"
        time.sleep(0.02)


def test_a_thread_that_starts_late_uses_up_the_bound(drip: DripServer) -> None:
    """The instant is taken before the call is scheduled (before ``pending``); the thread starts
    only after it has passed: the save's first request is refused before it connects."""
    drip.mode = "fast"
    at = time.monotonic() + 0.3
    outcome: list[BaseException | None] = []

    def late() -> None:
        time.sleep(0.5)  # queued behind other library calls
        try:
            with call_deadline_until(at):
                workout.save(spec_of(DRAFT), CREDS, 7)
        except BaseException as exc:
            outcome.append(exc)
        else:
            outcome.append(None)

    thread = threading.Thread(target=late, daemon=True)
    thread.start()
    thread.join(5)
    assert len(outcome) == 1
    assert isinstance(outcome[0], RequestDeadline)
    time.sleep(0.2)
    assert (drip.accepts, drip.paths) == (0, [])


def test_a_late_thread_keeps_only_what_is_left_of_the_bound(drip: DripServer) -> None:
    """Mutation counterpart: started late, ``call_deadline(bound)`` would grant the whole bound
    again; ``call_deadline_until`` grants only the rest."""
    at = time.monotonic() + 1.0
    time.sleep(0.4)
    with call_deadline_until(at):
        left = egress.request_deadline(30) - time.monotonic()
    with call_deadline(1.0):
        whole = egress.request_deadline(30) - time.monotonic()
    assert left == pytest.approx(0.6, abs=0.15)
    assert whole == pytest.approx(1.0, abs=0.15)
    with pytest.raises(TypeError, match="instant"), call_deadline_until(True):  # pyright: ignore[reportArgumentType]
        pass
