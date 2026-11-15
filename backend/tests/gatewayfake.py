"""The in-process fake SwedeBeat gateway (``gatewaycore``) over an httpx transport (ADR 0035
section 9): no socket, no network. ``gateway`` is a fake with two users and the test client
key; ``gateway_client(fake)`` is the production gateway client (``egress_gateway``'s builder,
through ``swedebeat_gateway_test_client``) for ``ORIGIN`` over the fake. Public test values only.
"""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from classbeat.egress_gateway import LOGIN_SECONDS, swedebeat_gateway_test_client
from gatewaycore import FakeGateway, Reply

# The production shape: plain http to a private address with a port (ADR 0035 section 1).
ORIGIN = "http://192.168.1.222:7355"
CLIENT_KEY = "fake-gateway-client-key-Zq4mR8"  # a public test value, searched for in outputs
EMAIL = "rider@example.test"
PASSWORD = "fake-gateway-password-Qv7"  # a public test value, searched for in every output
EMAIL_B = "second@example.test"
PASSWORD_B = "fake-gateway-password-Bk3"
DEVICE_ID = "0123456789abcdef"


class _Trickle(httpx.AsyncByteStream):
    def __init__(self, body: bytes, pause: float) -> None:
        self._body, self._pause = body, pause

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if not self._pause:
            yield self._body
            return
        for byte in self._body:
            await asyncio.sleep(self._pause)
            yield bytes([byte])


def transport(fake: FakeGateway) -> httpx.MockTransport:
    """The fake behind an httpx transport, with its delays and trickles."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raw = await request.aread()
        headers = {k.lower(): v for k, v in request.headers.items()}
        path = request.url.raw_path.decode("ascii")
        reply: Reply = fake.handle(request.method, path, headers, raw)
        if reply.delay:
            await asyncio.sleep(reply.delay)
        # A stream with the declared length, as a server sends it: never decoded on the way, so
        # a gzip answer reaches the client as the raw bytes.
        headers = {**reply.headers, "content-length": str(len(reply.body))}
        stream = _Trickle(reply.body, reply.trickle)
        return httpx.Response(reply.status, headers=headers, stream=stream)

    return httpx.MockTransport(handler)


def new_fake() -> FakeGateway:
    fake = FakeGateway({CLIENT_KEY})
    fake.add_user(EMAIL, PASSWORD)
    fake.add_user(EMAIL_B, PASSWORD_B)
    return fake


def gateway_client(
    fake: FakeGateway, *, total_seconds: float = LOGIN_SECONDS, origin: str = ORIGIN
) -> httpx.AsyncClient:
    """The production client builder for ``origin`` over the fake."""
    return swedebeat_gateway_test_client(origin, transport(fake), total_seconds=total_seconds)


@pytest.fixture
def gateway() -> FakeGateway:
    return new_fake()
