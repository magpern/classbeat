"""The client mark of a session (ADR 0003, amendment of 2026-11-05, #632).

A session remembers the coarse network and the browser family it was last seen from. When a
request's mark differs, ``sessions.resolve_session`` records it and clears the session's recent
authentication, MFA and step-up times; it never signs the session out, because a phone moves
between wifi and mobile data all day.

- The network is the rate-limit bucket of the client address (``bucket_ip``: IPv4 as it is, IPv6
  by its /64), stored only as an HMAC under the ``session-client`` key: the comparison needs
  equality, not the address.
- The browser family is ``<browser>/<os>`` from the ``User-Agent``, with no versions, so a browser
  update is not a move. A script that copies the victim's exact ``User-Agent`` is not told apart;
  the network still is.

A request without a peer address (``clientip.UNKNOWN_ADDRESS``; #926, #1023) has no network, so
its mark is ``PEERLESS``: nothing is compared with it, nothing is stored from it and it proves
nothing (``sessions``). A forwarded ``unknown`` is not peerless (``client_ip`` never returns it).
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from classbeat.auth.clientip import UNKNOWN_ADDRESS, bucket_ip
from classbeat.auth.keys import Keys, hmac_digest

EVENT_KIND = "session_location_changed"
# At most one event per session in this interval, so two networks that alternate (a phone, or a
# script and its victim) cannot fill ``auth_events``. Every move still clears the times.
EVENT_INTERVAL = timedelta(minutes=1)

# Checked in order: the first match wins (Edge and Opera also say "Chrome", Chrome says "Safari").
_BROWSERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("edge", ("Edg/", "EdgA/", "EdgiOS/")),
    ("opera", ("OPR/", "OPiOS/", "Opera")),
    ("firefox", ("Firefox/", "FxiOS/")),
    ("chrome", ("CriOS/", "Chrome/", "Chromium/")),
    ("safari", ("Safari/",)),
)
# iOS before macOS (an iPhone says "like Mac OS X"), Android and ChromeOS before Linux.
_SYSTEMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("windows", ("Windows",)),
    ("ios", ("iPhone", "iPad", "iPod")),
    ("macos", ("Macintosh", "Mac OS X")),
    ("android", ("Android",)),
    ("chromeos", ("CrOS",)),
    ("linux", ("Linux", "X11")),
)
# Only the start of the header is read: a browser's family words are near the front, and a long
# header costs nothing more to compare.
_AGENT_READ = 512


def _first(text: str, table: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    for name, needles in table:
        if any(needle in text for needle in needles):
            return name
    return "other"


def agent_family(user_agent: str | None) -> str:
    """``<browser>/<os>`` of a ``User-Agent``, for example ``chrome/android``; ``none/none``
    without one. Scripts (curl, python-requests, ...) are ``other/other``."""
    if not user_agent:
        return "none/none"
    text = user_agent[:_AGENT_READ]
    return f"{_first(text, _BROWSERS)}/{_first(text, _SYSTEMS)}"


@dataclass(frozen=True, slots=True, repr=False)
class ClientMark:
    """Where a request comes from, as a session stores it. ``repr`` hides the hash."""

    ip_hash: bytes
    agent: str

    def __repr__(self) -> str:
        return f"ClientMark(agent={self.agent!r})"


@dataclass(frozen=True, slots=True)
class Peerless:
    """The mark of a request without a peer address: there is none to compare or store."""


PEERLESS: Final = Peerless()

type RequestMark = ClientMark | Peerless


def client_mark(keys: Keys, ip: str, user_agent: str | None) -> RequestMark:
    """The mark of a request from ``ip`` (``client_ip``'s result) with ``user_agent``;
    ``PEERLESS`` when ``ip`` is ``UNKNOWN_ADDRESS``, never a mark of the word ``unknown``."""
    if ip == UNKNOWN_ADDRESS:
        return PEERLESS
    return ClientMark(hmac_digest(keys.session_client, bucket_ip(ip)), agent_family(user_agent))
