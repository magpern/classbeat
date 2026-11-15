"""``CLASSBEAT_PLAYER_BASE_URL`` (ADR 0034 section 2): each bad value refuses startup, the
message names the rule and never the value, and a good one is canonical."""

import pytest
from pydantic import ValidationError

from authtools import make_settings
from classbeat.player.base_url_policy import PlayerUrlRefused, player_base_url, player_host

URL = "postgresql+psycopg://u:p@127.0.0.1:1/db"

GOOD = [
    ("https://player.example.com", "https://player.example.com"),
    ("https://player.example.com/", "https://player.example.com"),
    ("https://player.example.com:443", "https://player.example.com"),
    ("https://player.example.com:443/", "https://player.example.com"),
    ("https://a-b.c1.example", "https://a-b.c1.example"),
    ("https://xn--bcher-kva.example", "https://xn--bcher-kva.example"),
]

BAD = [
    "",
    "player.example.com",
    "http://player.example.com",
    "HTTPS://player.example.com",
    "ftp://player.example.com",
    "https://",
    "https:///",
    "https://Player.example.com",
    "https://player.example.com:8443",
    "https://player.example.com:80",
    "https://player.example.com:",
    "https://player.example.com:0443x",
    "https://player.example.com/api",
    "https://player.example.com//",
    "https://player.example.com?x=1",
    "https://player.example.com/#top",
    "https://user@player.example.com",
    "https://user:pw@player.example.com",
    "https://player.example.com\\@evil.example",
    "https://player.example.com /",
    "https://player.example.com\n",
    "https://player​.example.com",
    "https://localhost",
    "https://localhost:443",
    "https://player.localhost",
    "https://player.local",
    "https://db.internal",
    "https://player",
    "https://127.0.0.1",
    "https://10.0.0.5",
    "https://[::1]",
    "https://[fd00::1]",
    "https://1.2.3.4.5",
    "https://player.example.123",
    "https://-player.example.com",
    "https://player-.example.com",
    "https://player..example.com",
    "https://player.example.com.",
    "https://pl_ayer.example.com",
    "https://bücher.example",
    "https://" + "a" * 64 + ".example",
    "https://" + ("a" * 60 + ".") * 5 + "example",
]


@pytest.mark.parametrize(("value", "canonical"), GOOD)
def test_a_good_value_becomes_its_canonical_base(value: str, canonical: str) -> None:
    assert player_base_url(value) == canonical
    assert f"https://{player_host(value)}" == canonical
    assert make_settings(URL, player_base_url=value).player_base_url == canonical


@pytest.mark.parametrize("value", BAD)
def test_a_bad_value_is_refused_without_naming_it(value: str) -> None:
    with pytest.raises(PlayerUrlRefused) as refused:
        player_base_url(value)
    message = str(refused.value)
    assert message.startswith("player_base_url must be ")
    if len(value) > len("https://"):
        assert value not in message


@pytest.mark.parametrize("value", BAD)
def test_a_bad_value_refuses_the_settings(value: str) -> None:
    with pytest.raises(ValidationError, match="player_base_url"):
        make_settings(URL, player_base_url=value)


def test_unset_means_the_player_is_off() -> None:
    assert make_settings(URL).player_base_url is None


def test_the_environment_variable_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLASSBEAT_PLAYER_BASE_URL", "http://player.example.com")
    with pytest.raises(ValidationError, match="player_base_url"):
        make_settings(URL)
    monkeypatch.setenv("CLASSBEAT_PLAYER_BASE_URL", "https://player.example.com/")
    assert make_settings(URL).player_base_url == "https://player.example.com"
