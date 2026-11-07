"""Passkey rules with no I/O (ADR 0019 sections 4, 6, 7 and 10, #772).

The numbers the routes and the store share, the label rule and the transports kept from what a
browser reports. Nothing here reads the database, the network or a request.
"""

from collections.abc import Iterable
from datetime import date

MAX_PASSKEYS = 10  # per user (section 1); refused at the register start and re-checked at finish
CHALLENGE_SECONDS = 300  # a challenge, its cookie and the options' ``timeout`` (section 6)
CHALLENGE_BYTES = 32
USER_HANDLE_BYTES = 32
LABEL_MAX = 64
RP_NAME = "classbeat"
# Section 7: the only transports stored (display only); anything else a browser reports is dropped.
KEPT_TRANSPORTS = ("usb", "nfc", "ble", "internal", "hybrid", "smart-card")
PURPOSES = ("register", "sign_in", "reauth")


def clean_label(value: str) -> str | None:
    """The label as stored: trimmed, 1 to ``LABEL_MAX`` characters, printable (no control or
    line characters, so it stays one line in a mail and on the page); ``None`` when it is not."""
    label = value.strip()
    if not 1 <= len(label) <= LABEL_MAX or not label.isprintable():
        return None
    return label


def default_label(today: date) -> str:
    """The label "Passkey" and the date, when the user gave none (section 4)."""
    return f"Passkey {today.isoformat()}"


def kept_transports(reported: Iterable[object]) -> list[str]:
    """The reported transports that section 7 keeps, each once, in the order of the list."""
    seen = {value for value in reported if isinstance(value, str)}
    return [name for name in KEPT_TRANSPORTS if name in seen]
