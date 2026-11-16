"""The in-process fake SwedeBeat gateway (``gatewaycore``) over an httpx transport (ADR 0035
section 9): no socket, no network. ``gateway`` is a fake with two users and the test client
key; ``gateway_client(fake)`` is the production gateway client (``egress_gateway``'s builder,
through ``swedebeat_gateway_test_client``) for ``ORIGIN`` over the fake. Public test values only.

``gateway_api`` puts the fake behind ``classbeat.swedebeat_gateway.opener.open_client`` and
``open_upload_client`` (the seams the web's login, the worker's match and the studio's writes open
their client through) for one test;
``GATEWAY_SETTINGS`` are the settings that make SwedeBeat available (the URL and the key).
``store_legacy_row`` writes a ``{token, device_id}`` row of the direct-SwedeBeat era (ADR 0015),
which ``repository.save`` no longer takes.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from classbeat.connections.row_crypto import seal_row
from classbeat.connections.tables import connections
from classbeat.context import UserContext
from classbeat.crypto.envelope import canonical_json
from classbeat.crypto.keks import KeyRing
from classbeat.db import user_session
from classbeat.egress_gateway import LOGIN_SECONDS, swedebeat_gateway_test_client
from classbeat.swedebeat_gateway import opener
from gatewaycore import FakeGateway, Reply

# The production shape: plain http to a private address with a port (ADR 0035 section 1).
ORIGIN = "http://192.168.1.222:7355"
CLIENT_KEY = "fake-gateway-client-key-Zq4mR8"  # a public test value, searched for in outputs
EMAIL = "rider@example.test"
PASSWORD = "fake-gateway-password-Qv7"  # a public test value, searched for in every output
EMAIL_B = "second@example.test"
PASSWORD_B = "fake-gateway-password-Bk3"
DEVICE_ID = "0123456789abcdef"
# What makes SwedeBeat available on a test server (ADR 0035 section 2): the URL and the key.
GATEWAY_SETTINGS: dict[str, object] = {
    "swedebeat_gateway_url": ORIGIN,
    "swedebeat_gateway_key": SecretStr(CLIENT_KEY),
}


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
        path = request.url.raw_path.decode("ascii")
        if fake._take(path.partition("?")[0], ("connect_error",)):  # pyright: ignore[reportPrivateUsage]
            raise httpx.ConnectError("injected: no connection", request=request)
        raw = await request.aread()
        headers = {k.lower(): v for k, v in request.headers.items()}
        reply: Reply = fake.handle(request.method, path, headers, raw)
        if reply.delay:
            await asyncio.sleep(reply.delay)
        if reply.drop:  # the work is done; the connection is cut before the answer
            raise httpx.ReadError("injected: connection lost", request=request)
        # A stream with the declared length, as a server sends it: never decoded on the way, so
        # a gzip answer reaches the client as the raw bytes.
        headers = dict(reply.headers)
        if not reply.no_length:
            length = len(reply.body) if reply.declared is None else reply.declared
            headers["content-length"] = str(length)
        stream = _Trickle(reply.body, reply.trickle)
        return httpx.Response(reply.status, headers=headers, stream=stream)

    return httpx.MockTransport(handler)


def new_fake() -> FakeGateway:
    fake = FakeGateway({CLIENT_KEY})
    fake.add_user(EMAIL, PASSWORD)
    fake.add_user(EMAIL_B, PASSWORD_B)
    return fake


def gateway_client(
    fake: FakeGateway,
    *,
    total_seconds: float = LOGIN_SECONDS,
    origin: str = ORIGIN,
    upload: bool = False,
) -> httpx.AsyncClient:
    """The production client builder for ``origin`` over the fake (``upload``: the
    merge-publish's, with its own 900 s deadline)."""
    return swedebeat_gateway_test_client(
        origin, transport(fake), total_seconds=total_seconds, upload=upload
    )


@pytest.fixture
def gateway() -> FakeGateway:
    return new_fake()


@pytest.fixture
def gateway_api(monkeypatch: pytest.MonkeyPatch, gateway: FakeGateway) -> FakeGateway:
    """The fake behind the production seam, with the deadline each caller asks for."""

    def open_client(total_seconds: float) -> httpx.AsyncClient:
        return gateway_client(gateway, total_seconds=total_seconds)

    def open_upload_client() -> httpx.AsyncClient:
        return gateway_client(gateway, upload=True)

    monkeypatch.setattr(opener, "open_client", open_client)
    monkeypatch.setattr(opener, "open_upload_client", open_upload_client)
    return gateway


def calls(fake: FakeGateway, path: str) -> list[Any]:
    """The requests the fake saw on ``path``, in order."""
    return [r for r in fake.requests if r.path == path]


async def store_legacy_row(
    factory: async_sessionmaker[AsyncSession], ring: KeyRing, ctx: UserContext
) -> UUID:
    """A ``connected`` SwedeBeat row with the ADR 0015 payload ``{token, device_id}`` and no
    ``expires_at``, sealed as ``repository.save`` sealed it then."""
    row_id = uuid.uuid4()
    payload = canonical_json({"token": "legacy-swedebeat-token-Hq2", "device_id": DEVICE_ID})
    async with user_session(factory, ctx) as db:
        sealed = await seal_row(db, ring, ctx.user_id, "swedebeat", row_id, payload)
        await db.execute(
            insert(connections).values(
                id=row_id,
                owner_id=ctx.user_id,
                provider="swedebeat",
                status="connected",
                public={"email": EMAIL},
                connected_at=func.now(),
                updated_at=func.now(),
                **sealed,
            )
        )
    return row_id


async def store_bearer(
    factory: async_sessionmaker[AsyncSession],
    ring: KeyRing,
    ctx: UserContext,
    fake: FakeGateway,
    email: str = EMAIL,
) -> str:
    """A connected SwedeBeat row holding a bearer the fake issued for ``email``, as a connect
    stores it (``{bearer}``, ``expires_at`` from the fake's lifetime, ``public {email}``)."""
    from datetime import UTC, datetime, timedelta

    from classbeat.connections import save

    bearer = fake.issue(email)
    expires_at = datetime.now(UTC) + timedelta(seconds=fake.ttl)
    await save(
        factory, ring, ctx, "swedebeat", {"bearer": bearer}, public={"email": email},
        expires_at=expires_at,
    )  # fmt: skip
    return bearer
