"""The track download (``client.download_track``, ADR 0036 sections 3.4 and 8, SR C2) against the
fake gateway: what it sends, the 60 MB cap held mid-stream whatever ``Content-Length`` says, the
type, encoding and redirect refusals, the reference pattern, ADR 0035 section 5's status table
with the download's own ``404`` and ``413``, the private destination, and that no failure leaves a
file, chains anything or carries a secret. Everything runs over an httpx transport or a loopback
socket (the netguard holds)."""

import asyncio
import hashlib
import os
import stat
import sys
import threading
import traceback
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO

import httpx
import pytest
from pydantic import SecretStr

from classbeat.egress import OutboundBlocked
from classbeat.egress_gateway import (
    DOWNLOAD_SECONDS,
    TOTAL_MAX_SECONDS,
    clamp_seconds,
    swedebeat_gateway_test_client,
)
from classbeat.swedebeat_gateway import client as client_module
from classbeat.swedebeat_gateway.client import Download, GatewayFailure, download_track
from classbeat.swedebeat_gateway.track_policy import DOWNLOAD_MAX
from gatewaycore import FIXTURE_MP3, FakeGateway, make_handler, reference_of
from gatewayfake import CLIENT_KEY, EMAIL, gateway_client, new_fake

KEY = SecretStr(CLIENT_KEY)
TITLE, ARTIST = "Levitating", "Dua Lipa"
REFERENCE = reference_of(TITLE, ARTIST)
ROUTE = f"/tracks/{REFERENCE}/download"


@pytest.fixture
def fake(gateway: FakeGateway) -> FakeGateway:
    gateway.add_track(TITLE, ARTIST, "found")
    return gateway


@pytest.fixture
def private(tmp_path: Path) -> Path:
    directory = tmp_path / "audio"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


def gone(path: Path) -> bool:
    return not path.exists()


def assert_clean(failure: BaseException, bearer: str) -> None:
    assert failure.__cause__ is None
    assert failure.__context__ is None
    text = "".join(traceback.format_exception(failure)) + repr(failure) + str(failure)
    for secret in (CLIENT_KEY, bearer):
        assert secret not in text


async def fetch(
    fake: FakeGateway,
    dest: Path,
    *,
    reference: str = REFERENCE,
    total_seconds: float = DOWNLOAD_SECONDS,
    max_bytes: int = DOWNLOAD_MAX,
) -> Download:
    bearer = fake.issue(EMAIL)
    async with gateway_client(fake, total_seconds=total_seconds) as client:
        return await download_track(client, KEY, bearer, reference, dest, max_bytes=max_bytes)


async def fails(fake: FakeGateway, dest: Path, **kwargs: object) -> GatewayFailure:
    bearer = fake.issue(EMAIL)
    async with gateway_client(fake) as client:
        with pytest.raises(GatewayFailure) as failed:
            await download_track(client, KEY, bearer, REFERENCE, dest, **kwargs)  # pyright: ignore[reportArgumentType]
    assert_clean(failed.value, bearer)
    assert gone(dest)  # no partial file is left behind
    return failed.value


# --- What is sent and kept -----------------------------------------------------------------


async def test_the_download_sends_one_get_with_the_key_the_bearer_and_no_query(
    fake: FakeGateway, private: Path
) -> None:
    dest = private / "track.mp3"
    bearer = fake.issue(EMAIL)
    async with gateway_client(fake, total_seconds=DOWNLOAD_SECONDS) as client:
        got = await download_track(client, KEY, bearer, REFERENCE, dest)
    assert got == Download(hashlib.sha256(FIXTURE_MP3).hexdigest(), len(FIXTURE_MP3))
    assert dest.read_bytes() == FIXTURE_MP3
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600
    [sent] = fake.requests
    assert (sent.method, sent.path, sent.query, sent.body) == ("GET", ROUTE, "", None)
    assert sent.headers == {
        "host": "192.168.1.222:7355",
        "accept": "audio/mpeg",
        "accept-encoding": "identity",
        "x-api-key": CLIENT_KEY,
        "authorization": f"Bearer {bearer}",
    }
    assert sent.email == EMAIL


async def test_a_media_type_parameter_is_accepted(fake: FakeGateway, private: Path) -> None:
    fake.inject("content_type", ROUTE, value="Audio/MPEG; charset=binary")
    assert (await fetch(fake, private / "t.mp3")).size == len(FIXTURE_MP3)


def test_the_download_has_its_own_deadline_within_the_clamp() -> None:
    assert DOWNLOAD_SECONDS == 120.0
    assert clamp_seconds(DOWNLOAD_SECONDS) == DOWNLOAD_SECONDS <= TOTAL_MAX_SECONDS


# --- The reference, the cap and the destination, before anything is sent -------------------


@pytest.mark.parametrize(
    "reference",
    ["", "a" * 65, "../etc", "a/b", "a b", "räf", "x?preview=true", "ref%2F", "a\n", ".", "a.b"],
)
async def test_a_reference_outside_the_pattern_is_refused_before_sending(
    fake: FakeGateway, private: Path, reference: str
) -> None:
    dest = private / "t.mp3"
    with pytest.raises(ValueError, match="reference"):
        await fetch(fake, dest, reference=reference)
    assert fake.requests == []
    assert gone(dest)


@pytest.mark.parametrize("reference", ["a", "A" * 64, "Ab_9-z"])
async def test_a_reference_inside_the_pattern_is_sent(
    fake: FakeGateway, private: Path, reference: str
) -> None:
    fake.add_audio(reference, b"\xff\xfb" * 10)
    assert (await fetch(fake, private / "t.mp3", reference=reference)).size == 20


@pytest.mark.parametrize("max_bytes", [0, -1, DOWNLOAD_MAX + 1])
async def test_the_cap_is_never_above_60_mb(
    fake: FakeGateway, private: Path, max_bytes: int
) -> None:
    with pytest.raises(ValueError, match="60 MB"):
        await fetch(fake, private / "t.mp3", max_bytes=max_bytes)
    assert fake.requests == []


async def test_an_existing_file_is_neither_written_nor_removed(
    fake: FakeGateway, private: Path
) -> None:
    dest = private / "t.mp3"
    dest.write_bytes(b"kept")
    with pytest.raises(ValueError, match="new file"):
        await fetch(fake, dest)
    assert dest.read_bytes() == b"kept"
    assert fake.requests == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and symlinks")
async def test_a_directory_that_is_not_private_is_refused(
    fake: FakeGateway, tmp_path: Path, private: Path
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    for dest in (shared / "t.mp3", link / "t.mp3", tmp_path / "absent" / "t.mp3"):
        with pytest.raises(ValueError, match="private directory"):
            await fetch(fake, dest)
    assert fake.requests == []
    assert os.listdir(shared) == [] and os.listdir(private) == []  # noqa: PT018


# --- The 60 MB cap, mid-stream ---------------------------------------------------------------


@pytest.mark.parametrize(
    "length", [{"kind": "declared", "size": 1000}, {"kind": "no_length"}, None]
)
async def test_the_cap_holds_mid_stream_whatever_content_length_says(
    fake: FakeGateway, private: Path, length: dict[str, object] | None
) -> None:
    fake.add_audio(REFERENCE, bytes(DOWNLOAD_MAX + 1))
    if length is not None:  # None: the true length declared, refused before a byte is read
        fake.inject(path=ROUTE, **length)  # pyright: ignore[reportArgumentType]
    assert (await fails(fake, private / "t.mp3")).category == "track_too_large"


async def test_exactly_60_mb_is_kept(fake: FakeGateway, private: Path) -> None:
    fake.add_audio(REFERENCE, b"\xff" * DOWNLOAD_MAX)
    fake.inject("no_length", ROUTE)
    got = await fetch(fake, private / "t.mp3")
    assert got.size == DOWNLOAD_MAX


async def test_a_smaller_cap_stops_the_read_at_once(fake: FakeGateway, private: Path) -> None:
    fake.inject("no_length", ROUTE)
    failure = await fails(fake, private / "t.mp3", max_bytes=len(FIXTURE_MP3) - 1)
    assert failure.category == "track_too_large"


async def test_a_declared_length_over_the_cap_is_refused_before_reading(
    fake: FakeGateway, private: Path
) -> None:
    fake.inject("declared", ROUTE, size=DOWNLOAD_MAX + 1)
    assert (await fails(fake, private / "t.mp3")).category == "track_too_large"


# --- Type, encoding, redirect -----------------------------------------------------------------


@pytest.mark.parametrize(
    "injection",
    [
        {"kind": "content_type", "value": "text/html"},
        {"kind": "content_type", "value": "application/octet-stream"},
        {"kind": "content_type", "value": "audio/mpeg3"},
        {"kind": "content_type", "value": "audio/mp4"},
        {"kind": "content_type", "value": ""},
        {"kind": "gzip"},
        {"kind": "declared", "size": len(FIXTURE_MP3) + 1},  # cut short
        {"kind": "declared", "size": 10},  # longer than declared
        {"kind": "body", "body": b""},  # empty, and JSON-typed
        {"kind": "raw", "status": 201, "body": FIXTURE_MP3},
        {"kind": "status", "status": 422},
        {"kind": "status", "status": 400},
    ],
)
async def test_a_wrong_type_encoding_length_or_status_is_invalid_response(
    fake: FakeGateway, private: Path, injection: dict[str, object]
) -> None:
    fake.inject(path=ROUTE, **injection)  # pyright: ignore[reportArgumentType]
    assert (await fails(fake, private / "t.mp3")).category == "invalid_response"


async def test_an_empty_audio_body_is_invalid_response(fake: FakeGateway, private: Path) -> None:
    fake.add_audio(REFERENCE, b"")
    assert (await fails(fake, private / "t.mp3")).category == "invalid_response"


@pytest.mark.parametrize(
    ("status", "location"),
    [(302, ROUTE), (307, "/auth/login"), (301, "http://evil.example/x.mp3"), (308, "x")],
)
async def test_a_redirect_is_outbound_blocked_and_not_followed(
    fake: FakeGateway, private: Path, status: int, location: str
) -> None:
    fake.inject("redirect", ROUTE, status=status, location=location)
    dest = private / "t.mp3"
    bearer = fake.issue(EMAIL)
    async with gateway_client(fake) as client:
        with pytest.raises(OutboundBlocked) as refused:
            await download_track(client, KEY, bearer, REFERENCE, dest)
    assert refused.value.after_request is True
    assert_clean(refused.value, bearer)
    assert len(fake.requests) == 1
    assert gone(dest)


# --- ADR 0035 section 5's statuses, and the download's own ------------------------------------


@pytest.mark.parametrize(
    ("injection", "category"),
    [
        ({"kind": "unknown_client"}, "app_misconfigured"),
        ({"kind": "bearer_401"}, "bearer_refused"),
        ({"kind": "status", "status": 401}, "bearer_refused"),
        ({"kind": "status", "status": 403}, "app_misconfigured"),
        ({"kind": "status", "status": 404}, "track_unavailable"),
        ({"kind": "status", "status": 413}, "track_too_large"),
        ({"kind": "status", "status": 502}, "upstream_unavailable"),
        ({"kind": "status", "status": 503}, "upstream_unavailable"),
        ({"kind": "rate_limited", "retry_after": "42"}, "rate_limited"),
    ],
)
async def test_each_status_maps_as_the_adr_says(
    fake: FakeGateway, private: Path, injection: dict[str, object], category: str
) -> None:
    fake.inject(path=ROUTE, **injection)  # pyright: ignore[reportArgumentType]
    failure = await fails(fake, private / "t.mp3")
    assert failure.category == category
    assert failure.retry_after == (42 if category == "rate_limited" else None)


async def test_a_reference_without_a_file_is_track_unavailable(
    fake: FakeGateway, private: Path
) -> None:
    dest = private / "t.mp3"
    bearer = fake.issue(EMAIL)
    async with gateway_client(fake) as client:
        with pytest.raises(GatewayFailure) as failed:
            await download_track(client, KEY, bearer, "ref-not-in-the-catalogue", dest)
    assert failed.value.category == "track_unavailable"
    assert gone(dest)


async def test_an_expired_bearer_or_a_wrong_key_is_mapped(fake: FakeGateway, private: Path) -> None:
    dest = private / "t.mp3"
    bearer = fake.issue(EMAIL)
    fake.expire(bearer)
    async with gateway_client(fake) as client:
        with pytest.raises(GatewayFailure) as failed:
            await download_track(client, KEY, bearer, REFERENCE, dest)
        assert failed.value.category == "bearer_refused"
        wrong = SecretStr("not-the-gateway-client-key-0001")
        with pytest.raises(GatewayFailure) as failed:
            await download_track(client, wrong, fake.issue(EMAIL), REFERENCE, dest)
        assert failed.value.category == "app_misconfigured"
    assert gone(dest)


async def test_a_timeout_or_a_trickle_past_the_deadline_is_upstream_unavailable(
    fake: FakeGateway, private: Path
) -> None:
    dest = private / "t.mp3"
    for kind, seconds in (("slow", 5.0), ("trickle", 0.05)):
        fake.inject(kind, ROUTE, seconds=seconds)
        bearer = fake.issue(EMAIL)
        async with gateway_client(fake, total_seconds=1) as client:
            with pytest.raises(GatewayFailure) as failed:
                await download_track(client, KEY, bearer, REFERENCE, dest)
        assert failed.value.category == "upstream_unavailable"
        assert_clean(failed.value, bearer)
        assert gone(dest)


async def test_a_refused_connection_is_upstream_unavailable(private: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    origin = "http://192.168.1.222:7355"
    async with swedebeat_gateway_test_client(origin, httpx.MockTransport(refuse)) as client:
        with pytest.raises(GatewayFailure) as failed:
            await download_track(client, KEY, "b" * 40, REFERENCE, private / "t.mp3")
    assert failed.value.category == "upstream_unavailable"
    assert_clean(failed.value, "b" * 40)


async def test_a_cancelled_download_leaves_no_file(fake: FakeGateway, private: Path) -> None:
    fake.inject("trickle", ROUTE, seconds=0.01)
    dest = private / "t.mp3"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(fetch(fake, dest), timeout=0.3)
    assert gone(dest)


class _CloseFails:
    """A file whose close closes it and then fails, as a flush onto a full disk would."""

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner

    def write(self, data: bytes) -> int:
        return self._inner.write(data)

    def flush(self) -> None:
        self._inner.flush()

    def close(self) -> None:
        self._inner.close()
        raise OSError(28, "No space left on device")


@pytest.mark.parametrize("injection", [None, {"kind": "status", "status": 404}])
async def test_a_close_that_fails_is_internal_and_leaves_no_file(
    fake: FakeGateway,
    private: Path,
    monkeypatch: pytest.MonkeyPatch,
    injection: dict[str, object] | None,
) -> None:
    real = client_module.create
    monkeypatch.setattr(client_module, "create", lambda path: _CloseFails(real(path)))
    if injection is not None:
        fake.inject(path=ROUTE, **injection)  # pyright: ignore[reportArgumentType]
    failure = await fails(fake, private / "t.mp3")
    assert failure.category == ("internal" if injection is None else "track_unavailable")


# --- The e2e fake serves the route --------------------------------------------------------------


@pytest.fixture
def loopback_gateway() -> Iterator[tuple[str, FakeGateway]]:
    """``gatewaycore.make_handler``, the handler ``e2e/fake-gateway/server.py`` serves, on a
    loopback socket (plain http here; the e2e server wraps the same handler in TLS)."""
    fake = new_fake()
    fake.add_track(TITLE, ARTIST, "found")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", fake
    finally:
        server.shutdown()
        server.server_close()


async def test_the_e2e_fakes_handler_serves_the_download(
    loopback_gateway: tuple[str, FakeGateway], private: Path
) -> None:
    origin, fake = loopback_gateway
    bearer = fake.issue(EMAIL)
    transport = httpx.AsyncHTTPTransport()
    async with swedebeat_gateway_test_client(
        origin, transport, total_seconds=DOWNLOAD_SECONDS
    ) as client:
        got = await download_track(client, KEY, bearer, REFERENCE, private / "t.mp3")
        assert got.sha256 == hashlib.sha256(FIXTURE_MP3).hexdigest()
        with pytest.raises(GatewayFailure) as failed:
            await download_track(client, KEY, bearer, "ref-unknown", private / "u.mp3")
        assert failed.value.category == "track_unavailable"
    assert [r.path for r in fake.requests] == [ROUTE, "/tracks/ref-unknown/download"]


def test_the_fixture_is_mpeg_audio_frames() -> None:
    frame = 417
    assert len(FIXTURE_MP3) % frame == 0
    assert all(FIXTURE_MP3[i : i + 2] == b"\xff\xfb" for i in range(0, len(FIXTURE_MP3), frame))
