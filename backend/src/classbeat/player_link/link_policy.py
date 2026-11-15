"""The link to classbeat-player, its pure rules (ADR 0034 section 3; player ADR 0002 section 7,
player ADR 0010 section 10). No I/O.

- The link request: the player's ``/link/source`` with ``source=classbeat``, the fixed
  ``redirect_uri`` (``<issuer>/connections/player/callback``), ``state``, an S256
  ``code_challenge`` and ``login_hint`` (the user's pairwise subject).
- The token answer: a ``cbp_src_`` source token and ``account_subject: {"iss", "sub"}``. The
  link is kept **only** when ``iss`` is classbeat's own issuer and ``sub`` the user's pairwise
  subject (``decide``); otherwise it is ended at once with that token and nothing is stored.
- Token shapes: ``cbp_<type>_`` and 43 base64url characters (player ADR 0002 section 2); a code
  that is not ``cbp_lc_`` is never sent.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Any, Final, Literal, NoReturn
from urllib.parse import urlencode

SOURCE_ID: Final = "classbeat"
CALLBACK_PATH: Final = "/connections/player/callback"
LINK_PATH: Final = "/link/source"
STATE_BYTES: Final = 32
VERIFIER_BYTES: Final = 32  # 43 characters
STATE_MAX: Final = 128  # a longer callback ``state`` is no state of ours
CODE_MAX: Final = 200
LINK_ID_MAX: Final = 64
_SECRET: Final = re.compile(r"[A-Za-z0-9_-]{43}")
_LINK_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")

# The callback's outcomes, shown on the account page (``/account/player?result=...``).
CONNECTED: Final = "connected"
MISMATCH: Final = "access_denied"  # every flow mismatch, an ``error`` from the player, a refusal
WRONG_ACCOUNT: Final = "wrong_account"  # "This player account belongs to someone else"
UNAVAILABLE: Final = "upstream_unavailable"
RATE_LIMITED: Final = "rate_limited"
INVALID_RESPONSE: Final = "invalid_response"
IN_PROGRESS: Final = "link_in_progress"  # another callback of the same user is linking now

TokenKind = Literal["lc", "src"]


def new_state() -> str:
    return secrets.token_urlsafe(STATE_BYTES)


def new_verifier() -> str:
    return secrets.token_urlsafe(VERIFIER_BYTES)


def challenge_of(verifier: str) -> str:
    """S256 (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def session_hash(session_id: object) -> bytes:
    """SHA-256 of the session id that started the flow (ADR 0009 section 3)."""
    return sha256(str(session_id))


def callback_uri(issuer: str) -> str:
    """The fixed redirect address the player is registered with."""
    return f"{issuer}{CALLBACK_PATH}"


def link_url(
    *, player_base_url: str, redirect_uri: str, state: str, verifier: str, login_hint: str
) -> str:
    params = {
        "source": SOURCE_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge_of(verifier),
        "login_hint": login_hint,
    }
    return f"{player_base_url.rstrip('/')}{LINK_PATH}?{urlencode(params)}"


def is_token(kind: TokenKind, value: object) -> bool:
    """Exactly a well-formed ``cbp_<kind>_`` token."""
    if not isinstance(value, str):
        return False
    head = f"cbp_{kind}_"
    return value.startswith(head) and _SECRET.fullmatch(value[len(head) :]) is not None


@dataclass(frozen=True, slots=True, eq=False)
class Decision:
    """What the token answer allows. ``keep``: store ``token`` (and ``link_id``); ``end``: call
    ``DELETE /api/source/link`` with ``token`` and store nothing; ``invalid``: no usable token
    (nothing can be ended). Never printed (the token)."""

    action: Literal["keep", "end", "invalid"]
    token: str | None = None
    link_id: str | None = None

    def __repr__(self) -> str:
        return f"Decision(action={self.action!r})"

    __str__ = __repr__

    def __reduce__(self) -> NoReturn:
        raise TypeError("a link decision is never serialized")


def _same(found: object, expected: str) -> bool:
    return isinstance(found, str) and hmac.compare_digest(
        found.encode("utf-8"), expected.encode("utf-8")
    )


def decide(answer: dict[str, Any], *, issuer: str, subject: str) -> Decision:
    """The binding (ADR 0034 section 3, step 4). A missing or malformed ``account_subject`` is
    a mismatch, never a pass: the link is ended."""
    token = answer.get("source_token")
    if not is_token("src", token):
        return Decision("invalid")
    assert isinstance(token, str)  # noqa: S101 - is_token checked it
    if str(answer.get("token_type", "")).lower() != "bearer":
        return Decision("end", token)
    bound = answer.get("account_subject")
    if not isinstance(bound, dict):
        return Decision("end", token)
    iss, sub = bound.get("iss"), bound.get("sub")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not (_same(iss, issuer) and _same(sub, subject)):
        return Decision("end", token)
    link_id = answer.get("link_id")
    kept_id = link_id if isinstance(link_id, str) and _LINK_ID.fullmatch(link_id) else None
    return Decision("keep", token, kept_id)
