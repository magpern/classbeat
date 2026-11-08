"""The cross-device sign-in rule (ADR 0019, owner question 4, W9, #776). No I/O.

A sign-in whose assertion reports ``authenticatorAttachment: "cross-platform"`` (a phone over the
browser's QR code, or a security key) may be on a shared or gym computer, so its session gets a
24-hour absolute limit and the browser is not made a known device.

The attachment is client-reported and unsigned: an attacker controls it. So it is used only to
make the session stricter, never looser, and for nothing else. Only the exact string
``"cross-platform"`` counts; ``"platform"``, a missing value or anything else is the normal
sign-in (an unknown *string* never gets here: py_webauthn refuses it as ``format`` while parsing
the assertion).
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

CROSS_PLATFORM = "cross-platform"
CROSS_DEVICE_SESSION = timedelta(hours=24)  # absolute, from the sign-in


def is_cross_platform(credential: Mapping[str, Any]) -> bool:
    """True only when the credential JSON reports the ``cross-platform`` attachment."""
    return credential.get("authenticatorAttachment") == CROSS_PLATFORM
