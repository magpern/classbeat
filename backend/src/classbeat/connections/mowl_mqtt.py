"""The MQTT 3.1.1 subset of MOWL's QR sign-in (ADR 0024 section 4): connect, subscribe to one
topic at QoS 0, wait for the one message, disconnect.

It works on a stream pair that ``classbeat.egress.mowl_mqtt_open`` opened (TLS to the broker on
8883); this module never opens a connection. Only what the flow needs is spoken: ``CONNECT``
(clean session, username and password), ``CONNACK``, one ``SUBSCRIBE`` (one exact topic, no
wildcard), ``SUBACK``, ``PUBLISH`` (QoS 0, or QoS 1 answered with ``PUBACK``), ``PINGREQ`` /
``PINGRESP`` and ``DISCONNECT``. Anything else from the broker, a packet over
``MAX_PACKET_BYTES`` or a malformed one ends the wait with ``MqttError``.

``MqttError`` carries a fixed reason word only, never a packet's bytes, a topic or a payload:
the topic holds the ticket key and the payload is the session token.
"""

import asyncio
import contextlib
import secrets
import time
from typing import Final

PROTOCOL_LEVEL: Final = 4  # MQTT 3.1.1
KEEPALIVE_SECONDS: Final = 60
# A ping well inside the keepalive, so the broker never drops a quiet connection.
PING_SECONDS: Final = 25.0
# The largest packet accepted from the broker. The awaited one is a topic of 45 bytes and a
# payload of 36; nothing the flow needs is near this.
MAX_PACKET_BYTES: Final = 4096
_SUBSCRIBE_PACKET_ID: Final = 1

# Packet types (the high nibble of the first byte).
_CONNECT, _CONNACK, _PUBLISH, _PUBACK = 1, 2, 3, 4
_SUBSCRIBE, _SUBACK, _PINGREQ, _PINGRESP, _DISCONNECT = 8, 9, 12, 13, 14


class MqttError(Exception):
    """The broker refused, broke the protocol or closed the connection. ``args == (reason,)``,
    a fixed word: ``refused``, ``protocol`` or ``closed``. Never packet content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    if len(data) > 0xFFFF:
        raise ValueError("an MQTT string is at most 65535 bytes")
    return len(data).to_bytes(2, "big") + data


def _remaining_length(length: int) -> bytes:
    out = bytearray()
    while True:
        digit, length = length % 128, length // 128
        out.append(digit | (0x80 if length else 0))
        if not length:
            return bytes(out)


def _packet(first: int, body: bytes) -> bytes:
    return bytes([first]) + _remaining_length(len(body)) + body


def connect_packet(client_id: str, username: str, password: str) -> bytes:
    """``CONNECT``: protocol ``MQTT`` level 4, clean session, username and password flags."""
    flags = 0x80 | 0x40 | 0x02
    header = _string("MQTT") + bytes([PROTOCOL_LEVEL, flags]) + KEEPALIVE_SECONDS.to_bytes(2, "big")
    return _packet(
        _CONNECT << 4, header + _string(client_id) + _string(username) + _string(password)
    )


def subscribe_packet(topic: str) -> bytes:
    """``SUBSCRIBE`` to exactly ``topic`` at QoS 0. A wildcard is refused (``ValueError``)."""
    if not topic or any(c in topic for c in "#+\x00"):
        raise ValueError("an exact topic, no wildcard")
    body = _SUBSCRIBE_PACKET_ID.to_bytes(2, "big") + _string(topic) + b"\x00"
    return _packet((_SUBSCRIBE << 4) | 0x02, body)


PINGREQ: Final = bytes([_PINGREQ << 4, 0])
DISCONNECT: Final = bytes([_DISCONNECT << 4, 0])


def _puback(packet_id: bytes) -> bytes:
    return bytes([_PUBACK << 4, 2]) + packet_id


async def read_packet(reader: asyncio.StreamReader) -> tuple[int, int, bytes]:
    """One packet: ``(type, flags, body)``. ``MqttError("closed")`` at the end of the stream,
    ``MqttError("protocol")`` for a length over ``MAX_PACKET_BYTES`` or a bad length field."""
    try:
        first = (await reader.readexactly(1))[0]
        length, multiplier = 0, 1
        for _ in range(4):
            byte = (await reader.readexactly(1))[0]
            length += (byte & 0x7F) * multiplier
            if not byte & 0x80:
                break
            multiplier *= 128
        else:
            raise MqttError("protocol")
        if length > MAX_PACKET_BYTES:
            raise MqttError("protocol")
        body = await reader.readexactly(length) if length else b""
    except asyncio.IncompleteReadError:
        raise MqttError("closed") from None
    return first >> 4, first & 0x0F, body


def _publish(flags: int, body: bytes) -> tuple[str, bytes, bytes | None]:
    """A ``PUBLISH`` body: ``(topic, payload, packet id or None)``. QoS 2 is refused."""
    qos = (flags >> 1) & 0x03
    if qos > 1 or len(body) < 2:
        raise MqttError("protocol")
    size = int.from_bytes(body[:2], "big")
    end = 2 + size
    if len(body) < end:
        raise MqttError("protocol")
    try:
        topic = body[2:end].decode("utf-8")
    except UnicodeDecodeError:
        raise MqttError("protocol") from None
    packet_id: bytes | None = None
    if qos == 1:
        if len(body) < end + 2:
            raise MqttError("protocol")
        packet_id, end = body[end : end + 2], end + 2
    return topic, body[end:], packet_id


async def _expect_connack(reader: asyncio.StreamReader) -> None:
    kind, _, body = await read_packet(reader)
    if kind != _CONNACK or len(body) != 2:
        raise MqttError("protocol")
    if body[1] != 0:  # 4: bad username or password, 5: not authorized, ...
        raise MqttError("refused")


async def _next_packet(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> tuple[int, int, bytes]:
    """The broker's next packet, sending a ``PINGREQ`` whenever ``PING_SECONDS`` pass quietly."""
    while True:
        try:
            async with asyncio.timeout(PING_SECONDS):
                return await read_packet(reader)
        except TimeoutError:
            pass
        writer.write(PINGREQ)
        await writer.drain()


async def wait_for_message(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    client_id: str,
    username: str,
    password: str,
    topic: str,
) -> bytes:
    """Connect, subscribe to ``topic`` and return the payload of the first message on it; then
    send ``DISCONNECT``. The caller bounds the whole wait (``asyncio.timeout``) and closes the
    streams. A message on any other topic is ignored (the broker should send none)."""
    writer.write(connect_packet(client_id, username, password))
    await writer.drain()
    await _expect_connack(reader)
    writer.write(subscribe_packet(topic))
    await writer.drain()
    subscribed = False
    while True:
        kind, flags, body = await _next_packet(reader, writer)
        if kind == _SUBACK:
            if (
                subscribed
                or body[:2] != _SUBSCRIBE_PACKET_ID.to_bytes(2, "big")
                or body[2:] != b"\x00"
            ):
                raise MqttError("refused" if body[2:] == b"\x80" else "protocol")
            subscribed = True
        elif kind == _PINGRESP:
            continue
        elif kind == _PUBLISH:
            got_topic, payload, packet_id = _publish(flags, body)
            if packet_id is not None:
                writer.write(_puback(packet_id))
            if got_topic != topic:
                continue
            with contextlib.suppress(OSError, ConnectionError):
                writer.write(DISCONNECT)
                await writer.drain()
            return payload
        else:
            raise MqttError("protocol")


def client_id(now: float | None = None) -> str:
    """A client id of the form the Windows app sends (``w0-<digits>-888``, seen on the wire in
    the 2026-11-05 capture): random enough that two waits never share one (a broker drops the
    older connection of a duplicate id)."""
    stamp = int((time.time() if now is None else now) * 1000)
    return f"w0-{stamp}{secrets.randbelow(10**6):06d}-888"
