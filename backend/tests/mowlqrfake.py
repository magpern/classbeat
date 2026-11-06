"""Fakes of MOWL's QR sign-in (ADR 0024, #647): the ticket, the image host and the broker. Never
the network: the broker is a plain TCP server on 127.0.0.1 (``egress.mowl_mqtt_open`` is
replaced by a connect to it for one test) and the image host an ``httpx.MockTransport`` under the
production QR image client's list and builder (``egress.mowl_qr_image_test_client``).

Public test values only; the key, the broker password and the token are searched for in every
output by the tests.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from classbeat import egress
from classbeat.connections import mowl_mqtt

TICKET_ID = 4242
KEY = "a1a1a1a1-b2b2-c3c3-d4d4-e5e5e5e5e5e5"  # the topic key (``Token``)
MQTT_PASSWORD = "fake-mqtt-Pw-77q"  # 15 characters, as MOWL's
QR_TOKEN = "cdcdcdcd-efef-0101-2323-454545454545"  # the session token the broker hands over
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def ticket_data(**overrides: Any) -> dict[str, Any]:
    """A ticket as MOWL's ``Data`` (captured shape, public values)."""
    ticket_id = overrides.pop("QrTicketID", TICKET_ID)
    info: dict[str, Any] = {
        "MQTTServerAddress": egress.MOWL_MQTT_HOST,
        "MQTTServerPort": 1883,
        "MQTTServerSSLPort": 8883,
        "MQTTServerWebsocketsPort": 8000,
        "ClientID": "fake-client",
        "Username": "ITCDefaultMqttUser",
        "Password": MQTT_PASSWORD,
        "MQTTFlowVersion": 1,
        "DisableSSL": False,
    }
    info.update(overrides.pop("info", {}))
    data: dict[str, Any] = {
        "QrTicketID": ticket_id,
        "DateCreated": "2026-11-05T10:00:00",
        "DateExpired": "2026-11-05T10:15:00",
        "DateConfirmed": None,
        "ConnectedUser": None,
        "ConnectedUserID": None,
        "Token": KEY,
        "QrImageUrl": f"https://media.mowl.com/qr/ticket/{ticket_id}",
        "MQTTConnectionInfo": info,
    }
    data.update(overrides)
    return data


def _string(body: bytes, at: int) -> tuple[str, int]:
    size = int.from_bytes(body[at : at + 2], "big")
    return body[at + 2 : at + 2 + size].decode(), at + 2 + size


@dataclass
class FakeBroker:
    """MOWL's broker for one test. ``scan()`` publishes ``payload`` on the subscribed topic (or
    ``publish_topic``) to every waiting client, as the phone's scan makes the broker do."""

    connack: int = 0
    suback: int = 0
    port: int = 0
    opened: int = 0
    connects: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    subscriptions: list[str] = field(default_factory=lambda: list[str]())
    disconnects: int = 0
    closed: int = 0
    publish_topic: str | None = None
    payload: bytes = QR_TOKEN.encode()
    open_error: BaseException | None = None
    _scanned: asyncio.Event = field(default_factory=asyncio.Event)
    _subscribed: asyncio.Event = field(default_factory=asyncio.Event)
    _server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2):
                    await self._server.wait_closed()

    def scan(self, payload: bytes | None = None) -> None:
        if payload is not None:
            self.payload = payload
        self._scanned.set()

    async def subscribed(self) -> None:
        async with asyncio.timeout(5):
            await self._subscribed.wait()

    async def open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """What replaces ``egress.mowl_mqtt_open``: a plain loopback connection."""
        self.opened += 1
        if self.open_error is not None:
            raise self.open_error
        return await asyncio.open_connection("127.0.0.1", self.port)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except (mowl_mqtt.MqttError, ConnectionError):
            pass

        finally:
            self.closed += 1
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        kind, _, body = await mowl_mqtt.read_packet(reader)
        assert kind == 1  # CONNECT
        name, at = _string(body, 0)
        level, flags = body[at], body[at + 1]
        keepalive = int.from_bytes(body[at + 2 : at + 4], "big")
        client_id, at = _string(body, at + 4)
        username, at = _string(body, at)
        password, at = _string(body, at)
        self.connects.append(
            {
                "protocol": name, "level": level, "flags": flags, "keepalive": keepalive,
                "client_id": client_id, "username": username, "password": password,
            }
        )  # fmt: skip
        writer.write(bytes([0x20, 2, 0, self.connack]))
        await writer.drain()
        if self.connack:
            return
        kind, flags, body = await mowl_mqtt.read_packet(reader)
        assert (kind, flags) == (8, 2)  # SUBSCRIBE
        topic, at = _string(body, 2)
        assert body[at:] == b"\x00"  # QoS 0
        self.subscriptions.append(topic)
        writer.write(bytes([0x90, 3]) + body[:2] + bytes([self.suback]))
        await writer.drain()
        self._subscribed.set()
        # Wait for the scan, or for the client to leave (a cancelled wait closes its connection).
        scanned = asyncio.ensure_future(self._scanned.wait())
        left = asyncio.ensure_future(reader.read(1))
        done, _ = await asyncio.wait({scanned, left}, return_when=asyncio.FIRST_COMPLETED)
        if left in done:
            scanned.cancel()
            return
        left.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await left  # the reader has one waiter at a time
        out = (self.publish_topic or topic).encode()
        packet = len(out).to_bytes(2, "big") + out + self.payload
        writer.write(bytes([0x30, len(packet)]) + packet)
        await writer.drain()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(2):
                kind, _, _ = await mowl_mqtt.read_packet(reader)
                if kind == 14:
                    self.disconnects += 1


@pytest.fixture
async def broker(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeBroker]:
    fake = FakeBroker()
    await fake.start()
    monkeypatch.setattr(egress, "mowl_mqtt_open", fake.open)
    try:
        yield fake
    finally:
        await fake.stop()


@dataclass
class FakeImageHost:
    """``media.mowl.com`` behind the production list: answers ``content`` with ``status``."""

    status: int = 200
    content: bytes = PNG
    headers: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    requests: list[httpx.Request] = field(default_factory=lambda: list[httpx.Request]())

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # A raw stream: the body reaches the client as sent (an encoding is not decoded here).
        stream = httpx.ByteStream(self.content)
        return httpx.Response(self.status, stream=stream, headers=self.headers)

    def client(self) -> httpx.AsyncClient:
        return egress.mowl_qr_image_test_client(httpx.MockTransport(self.handler))


@pytest.fixture
def image_host(monkeypatch: pytest.MonkeyPatch) -> FakeImageHost:
    fake = FakeImageHost()
    factory: Callable[[], httpx.AsyncClient] = fake.client
    monkeypatch.setattr(egress, "mowl_qr_image_async_client", factory)
    return fake
