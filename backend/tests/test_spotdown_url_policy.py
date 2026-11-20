"""``CLASSBEAT_SPOTDOWN_URL`` (ADR 0040 section 10, ``classbeat.spotdown.url_policy``): an internal
origin only, a single-label service name or a loopback or private IP literal, with an explicit
port; a refusal names the rule and never the value."""

import pytest

from classbeat.spotdown.url_policy import (
    SpotdownOrigin,
    SpotdownUrlRefused,
    spotdown_origin,
    spotdown_url,
)


@pytest.mark.parametrize(
    ("value", "origin"),
    [
        ("http://spotdown-api:8000", SpotdownOrigin("http", "spotdown-api", 8000)),
        ("http://spotdown-api:8000/", SpotdownOrigin("http", "spotdown-api", 8000)),
        ("https://spotdown:8443", SpotdownOrigin("https", "spotdown", 8443)),
        ("http://s:1", SpotdownOrigin("http", "s", 1)),
        ("http://10.0.0.5:8000", SpotdownOrigin("http", "10.0.0.5", 8000)),
        ("http://172.18.0.4:8000", SpotdownOrigin("http", "172.18.0.4", 8000)),
        ("http://192.168.1.2:8000", SpotdownOrigin("http", "192.168.1.2", 8000)),
        ("http://127.0.0.1:8000", SpotdownOrigin("http", "127.0.0.1", 8000)),
        ("http://[::1]:8000", SpotdownOrigin("http", "::1", 8000)),
        ("http://[fd00::4]:8000", SpotdownOrigin("http", "fd00::4", 8000)),
    ],
)
def test_an_internal_origin_is_accepted(value: str, origin: SpotdownOrigin) -> None:
    assert spotdown_origin(value) == origin
    assert spotdown_url(value) == origin.url


REFUSED = [
    "spotdown-api:8000",  # no scheme
    "ftp://spotdown-api:8000",
    "http://spotdown-api",  # no port
    "https://spotdown-api",
    "http://spotdown-api:",
    "http://spotdown-api:0",
    "http://spotdown-api:65536",
    "http://spotdown-api:8000/v2",  # a path
    "http://spotdown-api:8000?x=1",
    "http://spotdown-api:8000#f",
    "http://user:pw@spotdown-api:8000",
    "http://user@spotdown-api:8000",
    "http://spotdown-api%2e:8000",
    "http://[fd00::4%eth0]:8000",
    "http://spot down:8000",
    "http://Spotdown-api:8000",  # not lower case
    "http://spotdown.example.com:8000",  # a dotted (possibly public) name
    "http://spotdown.internal:8000",
    "http://localhost:8000",
    "http://-spotdown:8000",
    "http://spotdown-:8000",
    "http://2130706433:8000",  # an integer address
    "http://0x7f000001:8000",
    "http://017700000001:8000",
    "http://8.8.8.8:8000",  # a public address
    "http://169.254.169.254:80",  # link-local (cloud metadata)
    "http://0.0.0.0:8000",
    "http://224.0.0.1:8000",
    "http://255.255.255.255:8000",
    "http://10.0:8000",  # a short form
    "http://[2001:db8::1]:8000",
    "http://[fe80::1]:8000",
    "http://[FD00::4]:8000",
    "http://spotdown-api:8000\\@evil",
    "http://" + "a" * 64 + ":8000",  # a label over 63
    "http://spotdown-api:8000" + "/" * 300,
    "http://spotdown-api:8000\n",
]


@pytest.mark.parametrize("value", REFUSED)
def test_anything_else_is_refused_without_the_value_in_the_message(value: str) -> None:
    with pytest.raises(SpotdownUrlRefused) as refused:
        spotdown_origin(value)
    message = str(refused.value)
    assert message.startswith("spotdown_url must be ")
    assert value.strip() not in message
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
