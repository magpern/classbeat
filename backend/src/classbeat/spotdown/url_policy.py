"""``CLASSBEAT_SPOTDOWN_URL``: spotdown's origin (ADR 0040 section 10). Pure: no I/O.

spotdown runs as extra services in the same compose project, on the internal network with no
published port, so its origin is a deploy-time setting of the operator (as the player's host,
ADR 0034 section 2, and the gateway's origin, ADR 0035 section 1). This amends the rule in
``classbeat.egress`` that "the list is code, not a setting" for one more origin. The player's rule
(``https``, 443, a public DNS name) refuses an internal origin such as ``http://spotdown-api:8000``
on purpose, so spotdown has this rule of its own, **for an internal origin only**:

- an origin: ``http`` or ``https``, an **explicit port** (1 to 65535), no userinfo, no path but
  ``/``, no query, no fragment and no ``%`` (an IPv6 zone id or a percent-encoded host);
- the host is **one** of:

  - a compose service name: a **single** lowercase DNS label that starts with a letter (``[a-z]``,
    then letters, digits and inner ``-``, at most 63 characters), never ``localhost``. A dotted
    name is refused (it could be a public name), and so is an all-digit or ``0x`` label (some
    resolvers read ``2130706433`` or ``0x7f000001`` as an address);
  - a loopback or private **IP literal** (the gateway's ``private_ip``: ``127.0.0.0/8``,
    ``10.0.0.0/8``, ``172.16.0.0/12``, ``192.168.0.0/16``, ``::1``, ``fc00::/7``; never
    link-local, ``0.0.0.0``, multicast or broadcast).

Anything else is ``SpotdownUrlRefused`` (a ``ValueError``): the message names the rule, never the
value, and the process refuses to start (``classbeat.config_features``). Unset (an empty value, the
default) means spotdown is off: nothing calls it (``classbeat.egress_spotdown`` refuses).

**Residual risk:** a single-label name is resolved by the container's resolver (Docker's embedded
DNS for a compose service). The setting is the operator's, never a user's; the client's one exact
origin, no redirects, a size cap and a total deadline bound what a mistake could reach.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import SplitResult, urlsplit

from classbeat.swedebeat_gateway.url_policy import private_ip

URL_MAX: Final = 300
_LABEL: Final = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
_REFUSED_NAMES: Final = frozenset({"localhost"})
_NAME: Final = "spotdown_url"

Scheme = Literal["http", "https"]


class SpotdownUrlRefused(ValueError):
    """``CLASSBEAT_SPOTDOWN_URL`` breaks a rule; the message names it, never the value."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"{_NAME} must be {rule}")


@dataclass(frozen=True, slots=True)
class SpotdownOrigin:
    """A checked origin: the scheme, the host as httpx names it (an IPv6 literal without its
    brackets) and the explicit port."""

    scheme: Scheme
    host: str
    port: int

    @property
    def url(self) -> str:
        """The canonical origin, ``scheme://host:port`` (IPv6 in brackets), without a slash."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


def _split(value: str) -> tuple[SplitResult, int, str]:
    """The parts, the explicit port and the host exactly as written (brackets kept)."""
    if len(value) > URL_MAX or not value.isprintable() or any(c.isspace() for c in value):
        raise SpotdownUrlRefused("a printable URL of at most 300 characters")
    if not value.startswith(("http://", "https://")):
        raise SpotdownUrlRefused("an http or https URL")
    if any(mark in value for mark in "?#@\\%"):
        raise SpotdownUrlRefused(
            "an origin without query, fragment, userinfo, zone id or percent-encoding"
        )
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        port, parts = None, None
    if parts is None or parts.netloc.endswith(":") or port == 0:
        # Outside the except block: the ValueError's text would hold the value.
        raise SpotdownUrlRefused("a URL with a valid port")
    if port is None:
        raise SpotdownUrlRefused("a URL with an explicit port")
    if parts.path not in ("", "/"):
        raise SpotdownUrlRefused("an origin, with no path but '/'")
    return parts, port, parts.netloc.rsplit(":", 1)[0]


def _internal_host(written: str) -> str | None:
    """The host of an internal origin (a service name or a private IP literal), else ``None``."""
    if written.startswith("[") and written.endswith("]"):
        literal = written[1:-1]
        return private_ip(literal) if literal == literal.lower() else None
    if _LABEL.fullmatch(written) and written not in _REFUSED_NAMES:
        return written
    if written and all(c.isdigit() or c == "." for c in written):
        return private_ip(written)  # an IPv4 literal, written in full dotted form
    return None


def spotdown_origin(value: str) -> SpotdownOrigin:
    """The checked origin of ``value``; ``SpotdownUrlRefused`` when it breaks a rule."""
    parts, port, written = _split(value)
    host = _internal_host(written)
    if host is None:
        raise SpotdownUrlRefused(
            "an internal origin: a single-label service name (not localhost) or a loopback or"
            " private IP address"
        )
    scheme: Scheme = "https" if parts.scheme == "https" else "http"
    return SpotdownOrigin(scheme, host, port)


def spotdown_url(value: str) -> str:
    """The setting's validator: the canonical origin (``SpotdownOrigin.url``) of ``value``."""
    return spotdown_origin(value).url
