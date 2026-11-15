"""``CLASSBEAT_PLAYER_BASE_URL``: where the player lives (ADR 0034 section 2). Pure: no I/O.

The owner hosts the player on a server of their own choosing, so its host is a setting (this
amends the rule in ``classbeat.egress`` that "the list is code, not a setting"). The value must be:

- ``https``, with port 443 or none, no userinfo, no path but ``/``, no query and no fragment;
- a lowercase DNS name with at least one dot, whose last label holds a letter;
- not an IP literal, not ``localhost``, and not under ``.local``, ``.internal`` or ``.localhost``.

Anything else is refused with ``ValueError`` (the message names the rule, never the value), and
the process refuses to start (``classbeat.config_features``). Unset means the player features are
off.

**Residual risk** (ADR 0034 section 2): a public name that resolves to a private address passes.
The setting is the operator's, at deploy time, never a user's; the player client's own one-host
list, no redirects and its size cap bound what such a mistake could reach.
"""

import ipaddress
import re
from typing import Final
from urllib.parse import urlsplit

SCHEME: Final = "https"
PORT: Final = 443
URL_MAX: Final = 300
HOST_MAX: Final = 253
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_LETTER = re.compile(r"[a-z]")
_LOCAL_NAMES: Final = frozenset({"localhost"})
_LOCAL_SUFFIXES: Final = (".local", ".internal", ".localhost")


class PlayerUrlRefused(ValueError):
    """``CLASSBEAT_PLAYER_BASE_URL`` breaks a rule; the message names it, never the value."""


def _host_refusal(host: str) -> str | None:
    if not host or len(host) > HOST_MAX:
        return "a DNS name of 1 to 253 characters"
    if host != host.lower():
        return "a lowercase DNS name"
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        return "a DNS name, not an IP address"
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return "a DNS name with at least one dot (letters, digits and '-')"
    if not _LETTER.search(labels[-1]):
        return "a DNS name whose last label holds a letter, not an IP address"
    if host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES):
        return "a public name, not localhost, .local, .internal or .localhost"
    return None


def _refusal(value: str) -> str | None:
    if len(value) > URL_MAX or not value.isprintable() or any(c.isspace() for c in value):
        return "a printable URL of at most 300 characters"
    if not value.startswith(f"{SCHEME}://"):
        return "an https URL"
    if any(mark in value for mark in "?#@\\"):
        return "a URL without query, fragment or userinfo"
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return "a URL with a valid port"
    if port not in (None, PORT) or (parts.netloc.endswith(":") and port is None):
        return "port 443 or none"
    if parts.path not in ("", "/"):
        return "no path but '/'"
    return _host_refusal(parts.netloc.rsplit(":", 1)[0] if port is not None else parts.netloc)


def player_base_url(value: str) -> str:
    """The canonical base, ``https://<host>`` (no port, no trailing slash), of a value that
    keeps every rule; ``PlayerUrlRefused`` otherwise."""
    refusal = _refusal(value)
    if refusal is not None:
        raise PlayerUrlRefused(f"player_base_url must be {refusal}")
    return f"{SCHEME}://{player_host(value)}"


def player_host(value: str) -> str:
    """The host of a value ``player_base_url`` accepted."""
    parts = urlsplit(value)
    hostname = parts.hostname
    if hostname is None:  # pragma: no cover - refused by ``player_base_url`` first
        raise PlayerUrlRefused("player_base_url must name a host")
    return hostname
