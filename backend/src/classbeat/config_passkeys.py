"""Passkey settings: the relying party (RP) ID and origin and their start-up check (ADR 0019 s. 3).

Pure (no I/O). Both values come only from settings read once at start, never from a request
header (``Host``, ``Origin``, ``X-Forwarded-Host`` or anything the proxy sets): ``relying_party``
takes the settings and nothing else. Re-exported from ``classbeat.config``.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from classbeat.config import Settings

# A lowercase DNS name: labels of letters, digits and inner hyphens, 1 to 63 characters, joined by
# single dots. No scheme, port, path, upper case, empty label or trailing dot can match.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DNS_NAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")


class PasskeySettingsRefused(ValueError):
    """The passkey settings are not allowed; the message names the rule only."""


@dataclass(frozen=True)
class RelyingParty:
    """What every ceremony uses: ``rp_id`` for the options and ``expected_rp_id``, ``origin`` for
    ``expected_origin`` (one exact value: scheme, host and port, no list or wildcard)."""

    rp_id: str
    origin: str


def rp_id_problem(rp_id: str, public_base_url: str) -> str | None:
    """Why ``rp_id`` cannot be the RP ID for ``public_base_url``, or ``None`` when it can.

    The RP ID is a lowercase DNS name of at most 253 characters (no scheme, port, path or trailing
    dot), not an IP literal, equal to the public host or a dot-suffix of it, and of at least two
    labels unless it is ``localhost`` or ends in ``.localhost``. No public-suffix list: a suffix
    such as ``github.io`` passes here and the browser refuses it (ADR 0019 section 3).
    """
    if len(rp_id) > 253 or _DNS_NAME.fullmatch(rp_id) is None:
        return "webauthn_rp_id must be a lowercase DNS name without scheme, port, path or final dot"
    if _is_ip_literal(rp_id):
        return "webauthn_rp_id must not be an IP address"
    host = (urlsplit(public_base_url).hostname or "").lower()
    if host != rp_id and not host.endswith(f".{rp_id}"):
        return "webauthn_rp_id must be the host of public_base_url or a parent domain of it"
    local = rp_id == "localhost" or rp_id.endswith(".localhost")
    if "." not in rp_id and not local:
        return "webauthn_rp_id must have at least two labels (or be localhost)"
    return None


def _is_ip_literal(name: str) -> bool:
    # ``ip_address`` takes only the dotted-quad form; a numeric last label (``127.1``, ``10.0.1``)
    # is an address to a browser's URL parser too, and is never a top-level domain.
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name.rsplit(".", 1)[-1].isdigit()
    return True


def check_passkey_settings(passkeys: bool, rp_id: str | None, public_base_url: str) -> None:
    """The start-up check: with ``passkeys`` on, ``rp_id`` must be set (owner answer 6) and pass
    ``rp_id_problem``; a set ``rp_id`` is checked whatever the flag says."""
    if passkeys and rp_id is None:
        raise PasskeySettingsRefused("passkeys needs CLASSBEAT_WEBAUTHN_RP_ID")
    if rp_id is not None and (problem := rp_id_problem(rp_id, public_base_url)) is not None:
        raise PasskeySettingsRefused(problem)


def relying_party(settings: "Settings") -> RelyingParty:
    """The RP ID (``webauthn_rp_id``, else the public host) and the exact public origin."""
    host = urlsplit(settings.public_base_url).hostname or ""
    return RelyingParty(
        rp_id=settings.webauthn_rp_id or host.lower(), origin=settings.public_base_url
    )
