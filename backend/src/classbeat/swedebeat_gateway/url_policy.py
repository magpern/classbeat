"""``CLASSBEAT_SWEDEBEAT_GATEWAY_URL``: the gateway's origin (ADR 0035 section 1). Pure: no I/O.

The gateway belongs to the owner's deployment, so its origin is a setting (as the player's host,
ADR 0034 section 2). The value must be an origin: no userinfo, no path but ``/``, no query and no
fragment. Then **one** of these must hold:

- ``http`` to a loopback or private **IP literal** (IPv4 ``127.0.0.0/8``, ``10.0.0.0/8``,
  ``172.16.0.0/12``, ``192.168.0.0/16``; IPv6 ``::1`` or ``fc00::/7``) with an explicit port. A
  DNS name is never allowed over plain http: it could resolve anywhere. Link-local
  (``169.254.0.0/16``, ``fe80::/10``), ``0.0.0.0``, multicast and broadcast are refused.
- ``https`` to a public lowercase DNS name (the player's name rules,
  ``classbeat.player.base_url_policy``), on 443 or an explicit port.

Anything else is ``GatewayUrlRefused`` (a ``ValueError``): the message names the rule, never the
value, and web and worker refuse to start. Unset means SwedeBeat is unavailable on this server.
"""

import ipaddress
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import SplitResult, urlsplit

from classbeat.player.base_url_policy import PlayerUrlRefused, player_base_url

URL_MAX: Final = 300
HTTPS_PORT: Final = 443
_IPV4_ALLOWED: Final = tuple(
    ipaddress.IPv4Network(n)
    for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_IPV6_ALLOWED: Final = (ipaddress.IPv6Network("::1/128"), ipaddress.IPv6Network("fc00::/7"))
_BROADCAST: Final = ipaddress.IPv4Address("255.255.255.255")
_NAME: Final = "swedebeat_gateway_url"

Scheme = Literal["http", "https"]


class GatewayUrlRefused(ValueError):
    """``CLASSBEAT_SWEDEBEAT_GATEWAY_URL`` breaks a rule; the message names it, never the value."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"{_NAME} must be {rule}")


@dataclass(frozen=True, slots=True)
class GatewayOrigin:
    """A checked origin: the scheme, the host as httpx names it (an IPv6 literal without its
    brackets, lower case) and the port (443 when an https value names none)."""

    scheme: Scheme
    host: str
    port: int

    @property
    def url(self) -> str:
        """The canonical origin, ``scheme://host:port`` (IPv6 in brackets), without a slash."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


def _private_ip(text: str) -> str | None:
    """The canonical text of a loopback or private address literal, else ``None``."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if address.is_link_local or address.is_multicast or address.is_unspecified:
        return None
    if isinstance(address, ipaddress.IPv4Address):
        allowed = address != _BROADCAST and any(address in n for n in _IPV4_ALLOWED)
    else:
        allowed = any(address in n for n in _IPV6_ALLOWED)
    return str(address) if allowed else None


def _split(value: str) -> tuple[SplitResult, int | None, str]:
    """The parts, the port and the host exactly as written (brackets kept)."""
    if len(value) > URL_MAX or not value.isprintable() or any(c.isspace() for c in value):
        raise GatewayUrlRefused("a printable URL of at most 300 characters")
    if not value.startswith(("http://", "https://")):
        raise GatewayUrlRefused("an http or https URL")
    if any(mark in value for mark in "?#@\\"):
        raise GatewayUrlRefused("an origin without query, fragment or userinfo")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        port, parts = None, None
    if parts is None or parts.netloc.endswith(":") or port == 0:
        # Outside the except block: the ValueError's text would hold the value.
        raise GatewayUrlRefused("a URL with a valid port")
    if parts.path not in ("", "/"):
        raise GatewayUrlRefused("an origin, with no path but '/'")
    written = parts.netloc.rsplit(":", 1)[0] if port is not None else parts.netloc
    return parts, port, written


def gateway_origin(value: str) -> GatewayOrigin:
    """The checked origin of ``value``; ``GatewayUrlRefused`` when it breaks a rule."""
    parts, port, written = _split(value)
    if parts.scheme == "http":
        literal = written[1:-1] if written.startswith("[") and written.endswith("]") else written
        host = _private_ip(literal) if literal == literal.lower() else None
        if host is None:
            raise GatewayUrlRefused(
                "a loopback or private IP address over http (not link-local, 0.0.0.0, multicast"
                " or broadcast), never a DNS name"
            )
        if port is None:
            raise GatewayUrlRefused("an http URL with an explicit port")
        return GatewayOrigin("http", host, port)
    base: str | None = None
    rule = ""
    try:
        # The player's DNS name rules on the host as written; the port is checked here.
        base = player_base_url(f"https://{written}")
    except PlayerUrlRefused as refused:
        rule = str(refused).removeprefix("player_base_url must be ")
    if base is None:
        raise GatewayUrlRefused(f"{rule} when it is https")  # outside the block: no chain
    return GatewayOrigin("https", base.removeprefix("https://"), port or HTTPS_PORT)


def gateway_url(value: str) -> str:
    """The setting's validator: the canonical origin (``GatewayOrigin.url``) of ``value``."""
    return gateway_origin(value).url
