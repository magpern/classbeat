"""Paddle's webhook signature (ADR 0032 section 1). Pure: no I/O, no clock, no logging.

Paddle signs every delivery with the destination's secret key and sends
`Paddle-Signature: ts=<unix seconds>;h1=<hex HMAC-SHA256>`, where the HMAC is over
`<ts>:<raw body>` exactly as received (the body is never re-serialized before the check). During
a secret rotation a header may carry several `h1` values, and the destination may hold several
secrets: any `h1` that matches any secret passes. Every comparison is `hmac.compare_digest`.

The caller passes `now` (unix seconds); a timestamp more than the replay window (5 minutes by
default) from `now`, in the past or the future, is refused. A duplicate inside the window is
harmless: the writer is idempotent on `(provider, event_id)`. Nothing here returns or raises the
header, the body or a secret, so a refusal can be logged by its reason code alone.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

HEADER: Final = "Paddle-Signature"
# ADR 0032 section 1: five minutes in both directions.
DEFAULT_WINDOW_S: Final = 300
# Bounds on what an unauthenticated header can make us do before any HMAC is computed.
MAX_HEADER_CHARS: Final = 1024
MAX_SIGNATURES: Final = 4
MAX_SECRETS: Final = 4

_TS = re.compile(r"[0-9]{1,12}")
_H1 = re.compile(r"[0-9a-f]{64}")

Reason = Literal["missing", "malformed", "outside_window", "mismatch"]


@dataclass(frozen=True)
class Verified:
    """The signature matched; `ts` is the signed timestamp (unix seconds)."""

    ts: int


@dataclass(frozen=True)
class Rejected:
    reason: Reason


def _parse(header: str) -> tuple[str, list[str]] | None:
    """`(ts text, [h1, ...])`, or `None` for anything that is not exactly one `ts` and 1 to
    `MAX_SIGNATURES` lowercase-hex `h1` and nothing else: no unknown key, no whitespace, no
    empty part (ADR 0032 section 1: the header is parsed strictly)."""
    if len(header) > MAX_HEADER_CHARS:
        return None
    stamps: list[str] = []
    signatures: list[str] = []
    for part in header.split(";"):
        key, sep, value = part.partition("=")
        if not sep:
            return None
        if key == "ts":
            stamps.append(value)
        elif key == "h1":
            signatures.append(value)
        else:
            return None
    if len(stamps) != 1 or not _TS.fullmatch(stamps[0]):
        return None
    if not 1 <= len(signatures) <= MAX_SIGNATURES:
        return None
    if not all(_H1.fullmatch(s) for s in signatures):
        return None
    return stamps[0], signatures


def _digest(secret: str, ts: str, body: bytes) -> str:
    """Over the `ts` text as received, so the message checked is the one that was signed."""
    return hmac.new(secret.encode(), ts.encode() + b":" + body, hashlib.sha256).hexdigest()


def _check_secrets(secrets: Sequence[str]) -> None:
    # A configuration error, not a request error: never let an empty secret verify anything.
    if not 1 <= len(secrets) <= MAX_SECRETS or not all(secrets):
        raise ValueError("Paddle webhook secrets: 1 to 4 non-empty values")


def verify(
    header: str | None,
    body: bytes,
    secrets: Sequence[str],
    *,
    now: int,
    window_s: int = DEFAULT_WINDOW_S,
) -> Verified | Rejected:
    """Check `header` against `body` with any of `secrets`, `ts` within `window_s` of `now`."""
    _check_secrets(secrets)
    if window_s < 0:
        raise ValueError("window_s must not be negative")
    if not header:
        return Rejected("missing")
    parsed = _parse(header)
    if parsed is None:
        return Rejected("malformed")
    ts_text, signatures = parsed
    ts = int(ts_text)
    if abs(now - ts) > window_s:
        return Rejected("outside_window")
    matched = False
    for secret in secrets:
        expected = _digest(secret, ts_text, body)
        for signature in signatures:
            # No early exit: every pair is compared, so timing does not say which one matched.
            matched |= hmac.compare_digest(expected, signature)
    return Verified(ts) if matched else Rejected("mismatch")


def sign(body: bytes, secret: str, *, ts: int) -> str:
    """The header Paddle would send for `body` at `ts` (for the fake client and the tests)."""
    _check_secrets([secret])
    return f"ts={ts};h1={_digest(secret, str(ts), body)}"
