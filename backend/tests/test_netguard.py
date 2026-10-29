"""The whole suite refuses non-loopback sockets before a packet leaves (``netguard``)."""

import socket

import pytest

from netguard import NetworkBlocked


@pytest.mark.parametrize("host", ["192.0.2.1", "93.184.216.34", "2001:db8::1"])
def test_connecting_to_a_public_address_is_refused_before_any_packet(host: str) -> None:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)  # a real attempt at TEST-NET would time out instead of raising ours
        with pytest.raises(NetworkBlocked, match="may not connect"):
            sock.connect((host, 443))
        with pytest.raises(NetworkBlocked):
            sock.connect_ex((host, 443))


def test_resolving_a_public_name_is_refused_before_any_dns_query() -> None:
    with pytest.raises(NetworkBlocked, match="may not resolve"):
        socket.getaddrinfo("api.anthropic.com", 443)
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("api.anthropic.com", 443), timeout=0.2)


def test_loopback_still_works() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.create_connection(server.getsockname(), timeout=2) as client:
            conn, _ = server.accept()
            conn.close()
            assert client.getpeername()[0] == "127.0.0.1"
    assert socket.getaddrinfo("localhost", 80)


@pytest.mark.allow_network
def test_the_marker_switches_the_guard_off_without_sending_anything() -> None:
    # Only proves the opt-out is wired: connecting to a closed loopback port fails normally.
    with socket.socket() as sock, pytest.raises(OSError):  # noqa: PT011
        sock.connect(("127.0.0.1", 1))
