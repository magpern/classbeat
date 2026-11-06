"""The MOWL QR sign-in's two outbound paths (ADR 0024 section 3 and 4, #647): the QR image client
(``media.mowl.com``, https on 443, no redirect, web only) and the one non-HTTP rule, the TLS
connection to MOWL's broker on ``(mq.intelligent-cycling.com, 8883)`` with no fallback. Never the
network: ``asyncio.open_connection`` is replaced where a connect would be made."""

import ssl
from typing import Any

import httpx
import pytest

from classbeat import egress
from classbeat.egress import (
    MOWL_MQTT_HOST,
    MOWL_MQTT_TLS_PORT,
    MOWL_QR_IMAGE_ALLOWLIST,
    MOWL_QR_IMAGE_HOST,
    MQTT_ALLOWLIST,
    SPOTIFY_HOSTS,
    WEB_URLLIB_HOSTS,
    WORKER_HOSTS,
    OutboundBlocked,
    check_mqtt,
    mowl_mqtt_open,
    mowl_mqtt_tls_context,
    mowl_qr_image_async_client,
    mowl_qr_image_test_client,
)
from egress_scan import scan_source

M = "classbeat.connections.mowl_qr"


def test_the_lists_are_literal_and_narrow() -> None:
    assert MOWL_QR_IMAGE_ALLOWLIST.hosts == frozenset({"media.mowl.com"})
    assert set(MQTT_ALLOWLIST) == {("mq.intelligent-cycling.com", 8883)}
    # Neither host is on a urllib list: the library's opener (with its ``authorization``) and the
    # worker never reach them.
    for hosts in (WORKER_HOSTS, WEB_URLLIB_HOSTS, SPOTIFY_HOSTS):
        assert MOWL_QR_IMAGE_HOST not in hosts
        assert MOWL_MQTT_HOST not in hosts


def test_check_mqtt_takes_the_tls_port_of_the_broker_only() -> None:
    check_mqtt(MOWL_MQTT_HOST, MOWL_MQTT_TLS_PORT)
    for host, port in [
        (MOWL_MQTT_HOST, 1883),  # the plain-MQTT fallback the Windows app uses
        (MOWL_MQTT_HOST, 8000),  # websockets
        (MOWL_MQTT_HOST, 443),
        ("MQ.INTELLIGENT-CYCLING.COM", 8883),
        ("evil.example", 8883),
        ("api.mowl.com", 8883),
        (MOWL_MQTT_HOST + ".evil.example", 8883),
    ]:
        with pytest.raises(OutboundBlocked):
            check_mqtt(host, port)


def test_the_broker_tls_context_verifies() -> None:
    context = mowl_mqtt_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


async def test_the_broker_connection_is_tls_on_8883_with_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_open(*args: Any, **kwargs: Any) -> tuple[str, str]:
        calls.append((args, kwargs))
        return "reader", "writer"

    monkeypatch.setattr(egress.asyncio, "open_connection", fake_open)
    assert await mowl_mqtt_open() == ("reader", "writer")  # pyright: ignore[reportUnknownArgumentType]
    [(args, kwargs)] = calls
    assert args == (MOWL_MQTT_HOST, 8883)
    context = kwargs["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname  # noqa: PT018
    assert kwargs["server_hostname"] == MOWL_MQTT_HOST


@pytest.mark.parametrize(
    "error", [ssl.SSLCertVerificationError("bad cert"), ConnectionRefusedError(), TimeoutError()]
)
async def test_a_failed_tls_connect_is_an_error_and_nothing_else_is_tried(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    calls: list[tuple[Any, ...]] = []

    async def fake_open(*args: Any, **kwargs: Any) -> None:
        calls.append(args)
        raise error

    monkeypatch.setattr(egress.asyncio, "open_connection", fake_open)
    with pytest.raises(type(error)):
        await mowl_mqtt_open()
    assert calls == [(MOWL_MQTT_HOST, 8883)]  # one attempt, never 1883


async def test_the_web_only_paths_refuse_inside_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[object] = []

    async def fake_open(*args: Any, **kwargs: Any) -> None:
        opened.append(args)

    monkeypatch.setattr(egress.asyncio, "open_connection", fake_open)
    monkeypatch.setattr("classbeat.egress._worker_process", True)
    with pytest.raises(OutboundBlocked) as mqtt:
        await mowl_mqtt_open()
    with pytest.raises(OutboundBlocked) as image:
        mowl_qr_image_async_client()
    assert (mqtt.value.host, image.value.host) == (MOWL_MQTT_HOST, MOWL_QR_IMAGE_HOST)
    assert opened == []


async def test_the_production_image_client_has_no_header_and_no_redirect() -> None:
    client = mowl_qr_image_async_client()
    try:
        assert client.follow_redirects is False
        assert list(client.headers) == []
    finally:
        await client.aclose()


async def test_the_image_client_refuses_another_host_scheme_or_port() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"x")

    async with mowl_qr_image_test_client(httpx.MockTransport(handler)) as client:
        for url in [
            "http://media.mowl.com/qr/ticket/1",
            "https://media.mowl.com:8443/qr/ticket/1",
            "https://api.mowl.com/qr/ticket/1",
            "https://user@media.mowl.com/qr/ticket/1",
            "https://evil.example/qr/ticket/1",
        ]:
            with pytest.raises(OutboundBlocked):
                await client.get(url)
        assert seen == []
        assert (await client.get("https://media.mowl.com/qr/ticket/1")).status_code == 200


async def test_the_image_client_follows_no_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://media.mowl.com/other"})

    async with mowl_qr_image_test_client(httpx.MockTransport(handler)) as client:
        response = await client.get("https://media.mowl.com/qr/ticket/1")
    assert response.status_code == 302


@pytest.mark.parametrize(
    "source",
    [
        "from classbeat.egress import mowl_mqtt_open\nmowl_mqtt_open('evil.example')",
        "from classbeat.egress import mowl_mqtt_open\nmowl_mqtt_open(port=1883)",
        "from classbeat import egress\negress.mowl_mqtt_open(ssl=None)",
        "from classbeat.egress import mowl_qr_image_async_client\n"
        "mowl_qr_image_async_client(inner=t)",
        "from classbeat.egress import mowl_qr_image_test_client\nmowl_qr_image_test_client(t)",
        "from classbeat.egress import mowl_qr_image_network_transport\n"
        "mowl_qr_image_network_transport()",
        "import asyncio\nasyncio.open_connection('mq.intelligent-cycling.com', 1883)",
        "async def f(loop, p):\n    await loop.create_connection(p, 'mq.example', 1883)",
    ],
)
def test_the_guard_refuses_other_ways_to_the_qr_hosts(source: str) -> None:
    assert scan_source(source, M) != []


@pytest.mark.parametrize(
    "source",
    [
        "from classbeat.egress import mowl_mqtt_open\nasync def f():\n    await mowl_mqtt_open()",
        "from classbeat import egress\negress.mowl_qr_image_async_client()",
    ],
)
def test_the_guard_allows_the_argument_free_factories(source: str) -> None:
    assert scan_source(source, M) == []
