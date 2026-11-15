"""``CLASSBEAT_SWEDEBEAT_GATEWAY_URL`` (ADR 0035 section 1): every acceptance and every refusal of
``swedebeat_gateway.url_policy``, and the start refused on a bad value without echoing it."""

import pytest
from pydantic import ValidationError

from classbeat.config import Settings
from classbeat.swedebeat_gateway.url_policy import (
    GatewayOrigin,
    GatewayUrlRefused,
    gateway_origin,
    gateway_url,
)

ACCEPTED = [
    # Production's value: plain http to a private address on the shared host, with its port.
    ("http://192.168.1.222:7355", GatewayOrigin("http", "192.168.1.222", 7355)),
    ("http://192.168.1.222:7355/", GatewayOrigin("http", "192.168.1.222", 7355)),
    ("http://10.0.0.1:80", GatewayOrigin("http", "10.0.0.1", 80)),
    ("http://10.255.255.254:8080", GatewayOrigin("http", "10.255.255.254", 8080)),
    ("http://172.16.0.1:7355", GatewayOrigin("http", "172.16.0.1", 7355)),
    ("http://172.31.255.1:7355", GatewayOrigin("http", "172.31.255.1", 7355)),
    ("http://127.0.0.1:7355", GatewayOrigin("http", "127.0.0.1", 7355)),
    ("http://127.8.9.10:1", GatewayOrigin("http", "127.8.9.10", 1)),
    ("http://[::1]:7355", GatewayOrigin("http", "::1", 7355)),
    ("http://[fc00::1]:7355", GatewayOrigin("http", "fc00::1", 7355)),
    ("http://[fd12:3456::abcd]:65535", GatewayOrigin("http", "fd12:3456::abcd", 65535)),
    # https to a public lowercase DNS name, on 443 or an explicit port.
    ("https://sb.classbeat.eu", GatewayOrigin("https", "sb.classbeat.eu", 443)),
    ("https://sb.classbeat.eu/", GatewayOrigin("https", "sb.classbeat.eu", 443)),
    ("https://sb.classbeat.eu:443", GatewayOrigin("https", "sb.classbeat.eu", 443)),
    ("https://sb.classbeat.eu:8443", GatewayOrigin("https", "sb.classbeat.eu", 8443)),
    # The e2e fake's compose alias.
    (
        "https://gateway.classbeat-e2e.test",
        GatewayOrigin("https", "gateway.classbeat-e2e.test", 443),
    ),
]


@pytest.mark.parametrize(("value", "origin"), ACCEPTED)
def test_an_accepted_value_gives_its_exact_origin(value: str, origin: GatewayOrigin) -> None:
    assert gateway_origin(value) == origin
    assert gateway_url(value) == origin.url
    assert gateway_origin(origin.url) == origin  # the canonical form is accepted again


def test_the_canonical_form_names_the_port_and_brackets_ipv6() -> None:
    assert gateway_url("http://192.168.1.222:7355/") == "http://192.168.1.222:7355"
    assert gateway_url("https://sb.classbeat.eu") == "https://sb.classbeat.eu:443"
    assert gateway_url("http://[::1]:7355") == "http://[::1]:7355"


REFUSED = [
    # Not http(s), or not printable / too long.
    ("ftp://192.168.1.222:21", "an http or https URL"),
    ("192.168.1.222:7355", "an http or https URL"),
    ("HTTP://192.168.1.222:7355", "an http or https URL"),
    ("", "an http or https URL"),
    ("http://192.168.1.222:7355 ", "printable"),
    ("http://192.168.1.222:\t7355", "printable"),
    ("https://" + "a" * 300 + ".example", "printable"),
    # Userinfo, a query, a fragment, a backslash, a path.
    ("http://user@192.168.1.222:7355", "without query, fragment or userinfo"),
    ("http://user:pw@192.168.1.222:7355", "without query, fragment or userinfo"),
    ("https://user@sb.classbeat.eu", "without query, fragment or userinfo"),
    ("http://192.168.1.222:7355/?x=1", "without query, fragment or userinfo"),
    ("http://192.168.1.222:7355/#x", "without query, fragment or userinfo"),
    ("http://192.168.1.222:7355\\@evil.example", "without query, fragment or userinfo"),
    ("http://192.168.1.222:7355/api", "no path but '/'"),
    ("https://sb.classbeat.eu/v1/", "no path but '/'"),
    ("http://192.168.1.222:7355//", "no path but '/'"),
    # A bad port.
    ("http://192.168.1.222:", "a valid port"),
    ("http://192.168.1.222:0", "a valid port"),
    ("http://192.168.1.222:65536", "a valid port"),
    ("http://192.168.1.222:x", "a valid port"),
    ("https://sb.classbeat.eu:", "a valid port"),
    # Plain http: an IP literal in a loopback or private range, with its port.
    ("http://192.168.1.222", "an explicit port"),
    ("http://[::1]", "an explicit port"),
    ("http://sb.classbeat.eu:7355", "never a DNS name"),
    ("http://localhost:7355", "never a DNS name"),
    ("http://gateway:7355", "never a DNS name"),
    ("http://8.8.8.8:80", "never a DNS name"),
    ("http://172.32.0.1:80", "never a DNS name"),
    ("http://172.15.255.255:80", "never a DNS name"),
    ("http://192.169.0.1:80", "never a DNS name"),
    ("http://11.0.0.1:80", "never a DNS name"),
    ("http://169.254.169.254:80", "not link-local"),  # the cloud metadata address
    ("http://169.254.1.1:7355", "not link-local"),
    ("http://[fe80::1]:7355", "not link-local"),
    ("http://0.0.0.0:7355", "0.0.0.0"),  # noqa: S104 - a refused value, nothing binds
    ("http://[::]:7355", "0.0.0.0"),  # noqa: S104 - a refused value, nothing binds
    ("http://224.0.0.1:7355", "multicast"),
    ("http://[ff02::1]:7355", "multicast"),
    ("http://255.255.255.255:7355", "broadcast"),
    ("http://[2001:db8::1]:7355", "never a DNS name"),  # a public IPv6
    ("http://[::ffff:192.168.1.222]:7355", "never a DNS name"),  # IPv4-mapped
    ("http://[FC00::1]:7355", "never a DNS name"),  # not lower case
    ("http://010.0.0.1:80", "never a DNS name"),  # octal-looking
    ("http://10.1:80", "never a DNS name"),  # shorthand
    ("http://0x0a000001:80", "never a DNS name"),
    ("http://2130706433:80", "never a DNS name"),  # 127.0.0.1 as a number
    # https: a public lowercase DNS name (the player's rules), never an IP.
    ("https://192.168.1.222:7355", "not an IP address when it is https"),
    ("https://8.8.8.8", "not an IP address when it is https"),
    ("https://[::1]:443", "not an IP address when it is https"),
    ("https://SB.classbeat.eu", "a lowercase DNS name when it is https"),
    ("https://localhost", "when it is https"),
    ("https://gateway.local", "not localhost, .local, .internal or .localhost when it is https"),
    ("https://gateway.internal", "not localhost, .local, .internal or .localhost"),
    ("https://gateway.localhost", "not localhost, .local, .internal or .localhost"),
    ("https://gateway", "at least one dot"),
    ("https://sb.classbeat.eu.", "at least one dot"),
    ("https://sb_x.classbeat.eu", "at least one dot"),
    ("https://-sb.classbeat.eu", "at least one dot"),
    ("https://sb.classbeat.123", "last label holds a letter"),
]


@pytest.mark.parametrize(("value", "rule"), REFUSED)
def test_a_value_that_breaks_a_rule_is_refused_by_name(value: str, rule: str) -> None:
    with pytest.raises(GatewayUrlRefused) as refused:
        gateway_origin(value)
    message = str(refused.value)
    assert message.startswith("swedebeat_gateway_url must be ")
    assert rule in message
    if value:
        assert value not in message  # the rule, never the value
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None  # nothing chained


def test_a_refusal_is_a_value_error() -> None:
    assert issubclass(GatewayUrlRefused, ValueError)


@pytest.mark.parametrize(
    "value", ["http://gw.secret-name.example:7355", "https://10.9.8.7", "http://169.254.169.254:80"]
)
def test_settings_refuse_to_start_without_echoing_the_value(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", value)
    with pytest.raises(ValidationError) as refused:
        Settings()
    text = str(refused.value)
    assert "swedebeat_gateway_url must be" in text
    assert value not in text
    host = value.split("//", 1)[1].split(":", 1)[0]
    assert host not in text


def test_settings_hold_the_canonical_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", "http://192.168.1.222:7355/")
    assert Settings().swedebeat_gateway_url == "http://192.168.1.222:7355"


def test_unset_or_empty_means_no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", raising=False)
    assert Settings().swedebeat_gateway_url is None
    monkeypatch.setenv("CLASSBEAT_SWEDEBEAT_GATEWAY_URL", "")
    assert Settings().swedebeat_gateway_url is None  # compose's ${SWEDEBEAT_GATEWAY_URL:-}
