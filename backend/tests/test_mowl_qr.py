"""MOWL QR sign-in without the database (ADR 0024, #647): the ticket check, the image fetch, the
MQTT subset against a fake loopback broker, and the per-user, per-session waits. Never the
network: the broker is ``mowlqrfake.FakeBroker`` on 127.0.0.1, the image host a mock transport
under the production client's list and builder."""

import asyncio
import copy
import logging
import pickle
import ssl
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from classbeat.config import Settings
from classbeat.connections import mowl_mqtt, mowl_qr
from classbeat.connections.errors import ConnectionFailure
from classbeat.connections.mowl_qr import QrImage, QrTicket, QrWaits, parse_ticket
from mowlqrfake import KEY, MQTT_PASSWORD, PNG, QR_TOKEN, TICKET_ID, FakeBroker, FakeImageHost
from mowlqrfake import ticket_data as data

SECRETS = (KEY, MQTT_PASSWORD, QR_TOKEN)
IMAGE = QrImage(PNG, "image/png")


def ticket() -> QrTicket:
    return parse_ticket(data())


# --- the ticket ---------------------------------------------------------------------------------


def test_a_captured_ticket_is_parsed_and_hides_its_secrets() -> None:
    parsed = ticket()
    assert (parsed.ticket_id, parsed.username) == (TICKET_ID, "ITCDefaultMqttUser")
    assert parsed.topic == f"qrticket/{KEY}"
    assert parsed.image_url == f"https://media.mowl.com/qr/ticket/{TICKET_ID}"
    for shown in (repr(parsed), str(parsed), f"{parsed}", repr([parsed])):
        assert all(secret not in shown for secret in SECRETS)
    with pytest.raises(TypeError):
        pickle.dumps(parsed)
    with pytest.raises(TypeError):
        copy.copy(parsed)
    with pytest.raises(TypeError):
        copy.deepcopy(parsed)


@pytest.mark.parametrize(
    "overrides",
    [
        {"QrTicketID": 0},
        {"QrTicketID": True},
        {"QrTicketID": "4242"},
        {"Token": "not-a-guid"},
        {"Token": None},
        {"Token": KEY + "/#"},
        {"QrImageUrl": "https://evil.example/qr/ticket/4242"},
        {"QrImageUrl": "https://media.mowl.com/qr/ticket/1"},
        {"QrImageUrl": "http://media.mowl.com/qr/ticket/4242"},
        {"MQTTConnectionInfo": None},
        {"info": {"MQTTServerAddress": "evil.example"}},
        {"info": {"MQTTServerAddress": "mq.intelligent-cycling.com.evil.example"}},
        {"info": {"MQTTServerSSLPort": 1883}},
        {"info": {"MQTTFlowVersion": 2}},
        {"info": {"Password": ""}},
        {"info": {"Password": None}},
        {"info": {"Password": "pw\nwith-newline"}},
        {"info": {"Password": "x" * 300}},
        {"info": {"Username": 5}},
    ],
)
def test_a_ticket_of_another_shape_is_refused_without_its_text(overrides: dict[str, Any]) -> None:
    with pytest.raises(mowl_qr.TicketRefused) as refused:
        parse_ticket(data(**overrides))
    assert refused.value.args == ("unexpected ticket",)


def test_disable_ssl_is_never_read() -> None:
    # The ticket may say TLS is off; the connection is TLS all the same (egress.mowl_mqtt_open).
    assert parse_ticket(data(info={"DisableSSL": True})).ticket_id == TICKET_ID


def test_a_non_mapping_is_refused() -> None:
    for value in (None, [], "x", 1):
        with pytest.raises(mowl_qr.TicketRefused):
            parse_ticket(value)


# --- the image ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        (PNG, "image/png"),
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (b"GIF89a....", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
        (b"<html><script>1</script></html>", None),
        (b"", None),
    ],
)
def test_the_image_type_comes_from_the_first_bytes(content: bytes, kind: str | None) -> None:
    assert mowl_qr.image_type(content) == kind


async def test_the_image_is_fetched_without_any_token(image_host: FakeImageHost) -> None:
    image = await mowl_qr.fetch_image(ticket())
    assert image == QrImage(PNG, "image/png")
    [request] = image_host.requests
    assert str(request.url) == f"https://media.mowl.com/qr/ticket/{TICKET_ID}"
    assert "authorization" not in request.headers
    assert all(secret not in str(request.url) for secret in SECRETS)


@pytest.mark.parametrize(
    ("status", "content", "headers", "category"),
    [
        (404, PNG, {}, "unknown"),
        (302, b"", {"Location": "https://media.mowl.com/x"}, "unknown"),
        (429, b"", {}, "upstream_unavailable"),
        (503, b"", {}, "upstream_unavailable"),
        (200, PNG, {"Content-Encoding": "gzip"}, "unknown"),
        (200, b"<svg/>", {"Content-Type": "image/svg+xml"}, "unknown"),
        (200, PNG + b"\x00" * mowl_qr.IMAGE_MAX_BYTES, {}, "unknown"),
    ],
)
async def test_an_unusable_image_is_a_category(
    image_host: FakeImageHost, status: int, content: bytes, headers: dict[str, str], category: str
) -> None:
    image_host.status, image_host.content, image_host.headers = status, content, headers
    with pytest.raises(ConnectionFailure) as failed:
        await mowl_qr.fetch_image(ticket())
    assert failed.value.category == category
    assert len(image_host.requests) == 1  # never a second hop


# --- MQTT ---------------------------------------------------------------------------------------


def test_the_connect_packet_is_mqtt_3_1_1_with_a_clean_session() -> None:
    packet = mowl_mqtt.connect_packet("w0-1-888", "user", "pw")
    assert packet[0] == 0x10
    assert packet[2:8] == b"\x00\x04MQTT"
    assert packet[8] == 4  # protocol level 4
    assert packet[9] == 0xC2  # username, password, clean session; no will
    assert packet.endswith(b"\x00\x04user\x00\x02pw")


@pytest.mark.parametrize("topic", ["qrticket/#", "qrticket/+", "#", "+/x", "", "a\x00b"])
def test_a_wildcard_subscription_is_never_built(topic: str) -> None:
    with pytest.raises(ValueError, match="no wildcard"):
        mowl_mqtt.subscribe_packet(topic)


def test_the_subscription_is_qos_0_to_one_exact_topic() -> None:
    packet = mowl_mqtt.subscribe_packet(f"qrticket/{KEY}")
    assert packet[0] == 0x82
    assert packet.endswith(f"qrticket/{KEY}".encode() + b"\x00")


def test_a_client_id_looks_like_the_apps() -> None:
    first, second = mowl_mqtt.client_id(1.0), mowl_mqtt.client_id(1.0)
    assert first.startswith("w0-1000") and first.endswith("-888")  # noqa: PT018
    assert first != second or len(first) == len("w0-1000000000-888")


async def test_an_oversized_or_truncated_packet_is_a_protocol_error() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(bytes([0x30]) + mowl_mqtt._remaining_length(mowl_mqtt.MAX_PACKET_BYTES + 1))  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(mowl_mqtt.MqttError) as big:
        await mowl_mqtt.read_packet(reader)
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x30\x05ab")
    reader.feed_eof()
    with pytest.raises(mowl_mqtt.MqttError) as cut:
        await mowl_mqtt.read_packet(reader)
    assert (big.value.args, cut.value.args) == (("protocol",), ("closed",))


async def _wait_on(broker: FakeBroker, topic: str = f"qrticket/{KEY}") -> bytes:
    reader, writer = await broker.open()
    try:
        return await mowl_mqtt.wait_for_message(
            reader, writer, client_id="w0-1-888", username="u", password=MQTT_PASSWORD, topic=topic
        )
    finally:
        writer.close()


async def test_the_message_on_the_topic_is_returned_and_the_client_disconnects(
    broker: FakeBroker,
) -> None:
    waiting = asyncio.create_task(_wait_on(broker))
    await broker.subscribed()
    broker.scan()
    assert await asyncio.wait_for(waiting, 5) == QR_TOKEN.encode()
    assert broker.subscriptions == [f"qrticket/{KEY}"]
    [connect] = broker.connects
    assert (connect["protocol"], connect["level"], connect["password"]) == (
        "MQTT",
        4,
        MQTT_PASSWORD,
    )
    for _ in range(50):
        if broker.disconnects:
            break
        await asyncio.sleep(0.02)
    assert broker.disconnects == 1


async def test_a_message_on_another_topic_is_ignored(broker: FakeBroker) -> None:
    broker.publish_topic = "qrticket/other"
    waiting = asyncio.create_task(_wait_on(broker))
    await broker.subscribed()
    broker.scan()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(waiting), 0.3)
    waiting.cancel()


@pytest.mark.parametrize(("connack", "suback"), [(5, 0), (4, 0), (0, 0x80)])
async def test_a_refusal_by_the_broker_is_refused(
    broker: FakeBroker, connack: int, suback: int
) -> None:
    broker.connack, broker.suback = connack, suback
    with pytest.raises(mowl_mqtt.MqttError) as refused:
        await asyncio.wait_for(_wait_on(broker), 5)
    assert refused.value.args == ("refused",)
    assert all(secret not in repr(refused.value) for secret in SECRETS)


# --- the waits ----------------------------------------------------------------------------------


async def until(check: Any, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


async def test_a_scan_hands_the_token_once_to_the_starting_session_only(
    broker: FakeBroker, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    waits = QrWaits()
    user, session = uuid4(), uuid4()
    wait = waits.start(user, session, ticket(), IMAGE)
    try:
        await broker.subscribed()
        assert broker.subscriptions == [f"qrticket/{KEY}"]
        assert broker.connects[0]["username"] == "ITCDefaultMqttUser"
        assert broker.connects[0]["password"] == MQTT_PASSWORD
        assert waits.get(uuid4(), session, wait.id) is None  # another user
        assert waits.get(user, uuid4(), wait.id) is None  # another session of the same user
        other_id = ("y" if wait.id[0] == "x" else "x") + wait.id[1:]  # always a different id
        assert waits.get(user, session, other_id) is None
        assert waits.get(user, session, wait.id) is wait
        broker.scan()
        await until(lambda: wait.state == "scanned")
        assert wait.take_token() == QR_TOKEN
        assert wait.take_token() is None
        assert wait.state == "storing"
        assert all(secret not in repr(wait) for secret in SECRETS)
    finally:
        await waits.close()
    assert broker.opened == 1
    logged = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert all(secret not in logged for secret in SECRETS)


async def test_a_token_nobody_takes_is_dropped(
    broker: FakeBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mowl_qr, "SCANNED_KEEP_SECONDS", 0.05)
    monkeypatch.setattr(mowl_qr, "FINISHED_KEEP_SECONDS", 0.05)
    waits = QrWaits()
    user, session = uuid4(), uuid4()
    wait = waits.start(user, session, ticket(), IMAGE)
    try:
        await broker.subscribed()
        broker.scan()
        await until(lambda: wait.state == "expired")
        assert wait.take_token() is None
        await until(lambda: len(waits) == 0)  # and the wait is forgotten
    finally:
        await waits.close()


async def test_the_wait_expires_with_the_ticket(
    broker: FakeBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mowl_qr, "TICKET_SECONDS", 0.2)
    waits = QrWaits()
    wait = waits.start(uuid4(), uuid4(), ticket(), IMAGE)
    try:
        await until(lambda: wait.state == "expired")
        assert (wait.reason, wait.take_token()) == (None, None)
    finally:
        await waits.close()
    await until(lambda: broker.closed == 1)  # the broker connection ended with it


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ssl.SSLCertVerificationError("certificate verify failed"), "upstream_unavailable"),
        (ConnectionRefusedError(), "upstream_unavailable"),
        (TimeoutError(), "upstream_unavailable"),
    ],
)
async def test_a_failed_connection_fails_the_wait_with_one_attempt(
    broker: FakeBroker, error: BaseException, reason: str
) -> None:
    broker.open_error = error
    waits = QrWaits()
    wait = waits.start(uuid4(), uuid4(), ticket(), IMAGE)
    try:
        await until(lambda: wait.state == "failed")
        assert wait.reason == reason
    finally:
        await waits.close()
    assert broker.opened == 1  # no second attempt, no other port


@pytest.mark.parametrize(
    "payload",
    [b"", b"x" * 36, QR_TOKEN.encode() + b"\n", b'{"Token":"' + QR_TOKEN.encode() + b'"}'],
)
async def test_a_payload_that_is_not_a_token_fails_the_wait(
    broker: FakeBroker, payload: bytes
) -> None:
    waits = QrWaits()
    wait = waits.start(uuid4(), uuid4(), ticket(), IMAGE)
    try:
        await broker.subscribed()
        broker.scan(payload)
        await until(lambda: wait.state == "failed")
        assert (wait.reason, wait.take_token()) == ("unknown", None)
    finally:
        await waits.close()


async def test_a_refused_subscription_fails_the_wait(broker: FakeBroker) -> None:
    broker.suback = 0x80
    waits = QrWaits()
    wait = waits.start(uuid4(), uuid4(), ticket(), IMAGE)
    try:
        await until(lambda: wait.state == "failed")
        assert wait.reason == "upstream_unavailable"
    finally:
        await waits.close()


async def test_a_new_ticket_or_a_cancel_ends_the_earlier_wait(broker: FakeBroker) -> None:
    waits = QrWaits()
    user, session = uuid4(), uuid4()
    first = waits.start(user, session, ticket(), IMAGE)
    try:
        await broker.subscribed()
        second = waits.start(user, session, ticket(), IMAGE)
        assert first.state == "cancelled"
        assert waits.get(user, session, first.id) is None
        await until(lambda: len(broker.subscriptions) == 2)
        assert not waits.cancel(user, uuid4(), second.id)  # another session cannot cancel it
        assert second.state == "waiting"
        assert waits.cancel(user, session, second.id)
        assert second.state == "cancelled"
        await until(lambda: broker.closed == 2)
        broker.scan()  # a scan after the cancel reaches nobody
        await asyncio.sleep(0.05)
        assert (first.take_token(), second.take_token()) == (None, None)
    finally:
        await waits.close()


async def test_too_many_waits_refuse_a_start(
    broker: FakeBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mowl_qr, "MAX_WAITS", 2)
    waits = QrWaits()
    try:
        waits.start(uuid4(), uuid4(), ticket(), IMAGE)
        waits.start(uuid4(), uuid4(), ticket(), IMAGE)
        with pytest.raises(ConnectionFailure) as refused:
            waits.start(uuid4(), uuid4(), ticket(), IMAGE)
        assert refused.value.category == "rate_limited"
    finally:
        await waits.close()
    assert len(waits) == 0


async def test_a_refused_start_leaves_the_users_earlier_wait_running(
    broker: FakeBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mowl_qr, "MAX_WAITS", 2)
    waits = QrWaits()
    user, session = uuid4(), uuid4()
    try:
        earlier = waits.start(user, session, ticket(), IMAGE)
        waits.start(uuid4(), uuid4(), ticket(), IMAGE)
        with pytest.raises(ConnectionFailure) as refused:
            waits.check_room()
        assert refused.value.category == "rate_limited"
        # Full: the user's restart is refused and the earlier wait is not cancelled.
        with pytest.raises(ConnectionFailure):
            waits.start(user, session, ticket(), IMAGE)
        assert earlier.state == "waiting"
        assert waits.get(user, session, earlier.id) is earlier
    finally:
        await waits.close()


async def test_a_ticket_request_goes_out_without_authorization(mowl_api: Any) -> None:
    parsed = await mowl_qr.request_ticket()
    assert parsed.ticket_id == TICKET_ID
    assert mowl_api.qr_authorizations == [None]


@pytest.mark.parametrize(
    ("status", "ticket_value", "category"),
    [
        (503, None, "upstream_unavailable"),
        (429, None, "rate_limited"),
        (401, None, "unknown"),
        (200, {"QrTicketID": 1}, "unknown"),
    ],
)
async def test_a_ticket_failure_is_a_category(
    mowl_api: Any, status: int, ticket_value: Any, category: str
) -> None:
    mowl_api.status["qr"], mowl_api.qr_ticket = status, ticket_value
    with pytest.raises(ConnectionFailure) as failed:
        await mowl_qr.request_ticket()
    assert failed.value.category == category
    assert failed.value.__cause__ is None and failed.value.__context__ is None  # noqa: PT018


def test_the_switch_is_off_by_default_and_on_web_only() -> None:
    root = Path(__file__).resolve().parents[2]
    assert Settings.model_fields["mowl_qr_enabled"].default is False
    for compose in ("compose.yaml", "compose.prod.yaml"):
        services = yaml.safe_load((root / compose).read_text("utf-8"))["services"]
        assert services["web"]["environment"]["CLASSBEAT_MOWL_QR_ENABLED"] == (
            "${MOWL_QR_ENABLED:-false}"
        )
        # The worker never signs anyone in: it neither gets the switch nor reaches the hosts.
        assert "CLASSBEAT_MOWL_QR_ENABLED" not in services["worker"]["environment"]
    assert "MOWL_QR_ENABLED=false" in (root / ".env.example").read_text("utf-8")
