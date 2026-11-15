"""The signed events to the player (ADR 0034 section 2; player ADR 0010 section 8). Pure: no I/O.

- **The token** is an OpenID Connect back-channel logout token: header ``typ`` ``logout+jwt``,
  ``alg`` ES256 and the ``kid``; claims ``iss``, ``aud`` (the client id), ``iat``, ``exp``
  (``iat`` + 5 minutes), ``jti``, ``sub`` and ``events``, with no ``nonce`` and no ``sid``.
  ``events`` holds exactly the back-channel logout event and one classbeat event, each ``{}``.
- **Retries**: after the n-th failed attempt the next one is ``min(30 s * 2^(n-1), 6 h)`` later,
  with +/- 10 % jitter, never after ``give_up_at`` (created + 7 days), and at most 40 attempts;
  then the event is ``failed``. Each attempt signs a new token with the same ``jti``.
- **Errors** are stored as a category (``last_error``), never an answer's body.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

type EventKind = Literal["account_deleted", "sessions_revoked"]

LOGOUT_TYPE: Final = "logout+jwt"
BACKCHANNEL_LOGOUT: Final = "http://schemas.openid.net/event/backchannel-logout"
# Pinned with the player (its ADR 0010 section 8, classbeat-player #485).
EVENT_URNS: Final[dict[EventKind, str]] = {
    "account_deleted": "urn:classbeat:player:account-deleted",
    "sessions_revoked": "urn:classbeat:player:sessions-revoked",
}
EVENTS_PATH: Final = "/api/classbeat/events"
FORM_FIELD: Final = "logout_token"
TOKEN_LIFETIME: Final = timedelta(minutes=5)
JTI_BYTES: Final = 32
LEASE: Final = timedelta(seconds=60)
GIVE_UP_AFTER: Final = timedelta(days=7)
MAX_ATTEMPTS: Final = 40
FIRST_DELAY: Final = timedelta(seconds=30)
LONGEST_DELAY: Final = timedelta(hours=6)
JITTER: Final = 0.10
FAILED_KEPT: Final = timedelta(days=30)

type ErrorCategory = Literal[
    "status_4xx",
    "status_5xx",
    "status_other",
    "redirect",
    "timeout",
    "too_large",
    "network",
    "blocked",
    "unsigned",
    "expired",
]


def logout_claims(
    *, issuer: str, client_id: str, subject: str, jti: str, kind: EventKind, now: datetime
) -> dict[str, Any]:
    issued = int(now.timestamp())
    return {
        "iss": issuer,
        "aud": client_id,
        "iat": issued,
        "exp": issued + int(TOKEN_LIFETIME.total_seconds()),
        "jti": jti,
        "sub": subject,
        "events": {BACKCHANNEL_LOGOUT: {}, EVENT_URNS[kind]: {}},
    }


def retry_delay(attempt: int, jitter: float) -> timedelta:
    """The wait after the ``attempt``-th failure (1-based); ``jitter`` in [-1, 1] scales the
    +/- 10 %."""
    if attempt < 1:
        raise ValueError("attempts count from 1")
    if not -1.0 <= jitter <= 1.0:
        raise ValueError("jitter is between -1 and 1")
    base = min(FIRST_DELAY * 2 ** min(attempt - 1, 30), LONGEST_DELAY)
    return base * (1.0 + JITTER * jitter)


@dataclass(frozen=True, slots=True)
class Retry:
    at: datetime


@dataclass(frozen=True, slots=True)
class GiveUp:
    pass


def after_failure(
    *, attempt: int, now: datetime, give_up_at: datetime, jitter: float
) -> Retry | GiveUp:
    """What follows the ``attempt``-th failed attempt: another one, never after ``give_up_at``,
    or ``failed`` once the 40 attempts are used or ``give_up_at`` has passed."""
    if attempt >= MAX_ATTEMPTS or now >= give_up_at:
        return GiveUp()
    return Retry(min(now + retry_delay(attempt, jitter), give_up_at))


def status_category(status: int) -> ErrorCategory | None:
    """``None`` for a ``2xx`` (delivered); else the category of the answer."""
    if 200 <= status < 300:
        return None
    if 300 <= status < 400:
        return "redirect"
    if 400 <= status < 500:
        return "status_4xx"
    if 500 <= status < 600:
        return "status_5xx"
    return "status_other"
