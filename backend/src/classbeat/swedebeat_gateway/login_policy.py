"""The login and session bodies and answers (ADR 0035 sections 3 and 5). Pure: no I/O.

- **The login request** is ``{"email", "password", "device_id", "force"}``. ``force`` is sent
  explicitly although today's gateway ignores it (section 4). The device id is the user's HMAC
  device id (ADR 0015 section 2), at most the gateway's 64 characters.
- **The login answer** is read through a field allowlist: ``bearer`` is 16 to 4096 printable ASCII
  characters without whitespace, ``expires_in`` an integer from 60 s to 400 days; ``customer_id``
  (the SwedeBeat account, #1117) is kept only as 1 to 64 ASCII letters, digits, ``-`` or ``_``
  (an integer is read as its digits), else ``None``, and the login still counts: it is never
  logged and only its keyed digest is stored (``swedebeat_account.account_ref_for``).
  ``token_type`` and every other field are dropped. Any other shape is ``None`` (``unknown``).
- **The session answer** (``GET /auth/session``) is valid only as ``{"valid": true, ...}``.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

LOGIN_ANSWER_MAX: Final = 16 * 1024
SESSION_ANSWER_MAX: Final = 16 * 1024
BEARER_MIN: Final = 16
BEARER_MAX: Final = 4096
EXPIRES_MIN: Final = 60
EXPIRES_MAX: Final = 400 * 24 * 3600
DEVICE_ID_MAX: Final = 64
EMAIL_MIN: Final = 3
EMAIL_MAX: Final = 254
PASSWORD_MAX: Final = 256
CUSTOMER_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")


@dataclass(frozen=True, slots=True)
class LoginAnswer:
    """What a login keeps: the bearer and the SwedeBeat account (neither in ``repr``) and the
    bearer's lifetime in seconds."""

    bearer: str = field(repr=False)
    expires_in: int
    customer_id: str | None = field(default=None, repr=False)


def _visible_ascii(value: str) -> bool:
    return all("\x21" <= c <= "\x7e" for c in value)


def login_body(email: str, password: str, device_id: str, *, force: bool) -> bytes:
    """The login request's JSON. A value outside the gateway's bounds is a ``ValueError`` that
    names the field, never the value (the form bounds them first, so it is a programming error)."""
    if not EMAIL_MIN <= len(email) <= EMAIL_MAX:
        raise ValueError("the email is 3 to 254 characters")
    if not 1 <= len(password) <= PASSWORD_MAX:
        raise ValueError("the password is 1 to 256 characters")
    if not (1 <= len(device_id) <= DEVICE_ID_MAX and _visible_ascii(device_id)):
        raise ValueError("the device id is 1 to 64 printable ASCII characters")
    if not isinstance(force, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("force is a boolean")
    body = {"email": email, "password": password, "device_id": device_id, "force": force}
    return json.dumps(body).encode("ascii")


def _object(body: bytes | None, cap: int) -> dict[str, Any] | None:
    if body is None or len(body) > cap:
        return None
    try:
        answer = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return answer if isinstance(answer, dict) else None  # pyright: ignore[reportUnknownVariableType]


def parse_login(body: bytes | None) -> LoginAnswer | None:
    """The kept fields of a ``200`` login answer, or ``None`` for any other shape."""
    answer = _object(body, LOGIN_ANSWER_MAX)
    if answer is None:
        return None
    bearer, expires_in = answer.get("bearer"), answer.get("expires_in")
    if not isinstance(bearer, str) or not BEARER_MIN <= len(bearer) <= BEARER_MAX:
        return None
    if not _visible_ascii(bearer):
        return None
    if isinstance(expires_in, bool) or not isinstance(expires_in, int):
        return None
    if not EXPIRES_MIN <= expires_in <= EXPIRES_MAX:
        return None
    return LoginAnswer(bearer, expires_in, customer_id_of(answer.get("customer_id")))


def customer_id_of(value: object) -> str | None:
    """The account id of a login or session answer, or ``None`` when absent or malformed."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        value = str(value)
    if not isinstance(value, str) or CUSTOMER_ID.fullmatch(value) is None:
        return None
    return value


def parse_session(body: bytes | None) -> bool:
    """A ``200`` session answer says the bearer is valid: ``{"valid": true}`` (other fields are
    dropped)."""
    answer = _object(body, SESSION_ANSWER_MAX)
    return answer is not None and answer.get("valid") is True
