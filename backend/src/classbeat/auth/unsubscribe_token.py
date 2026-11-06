"""The signed link in optional mail (ADR 0022 section 5, ADR 0003 section 8; ME4 #655).

``b64url(v1|<user id>|<scope>|<expiry>)`` + ``.`` + ``b64url(HMAC-SHA256(key, payload))`` under the
``mail-unsubscribe`` derived key. Stateless: nothing is stored, and the token can do exactly one
thing, turn ``scope`` off for the user it names (``scope`` is a switchable category or ``all``;
never an essential kind, never "turn on", never anything else). So a leaked mail gives an attacker
at most the ability to stop that person's optional mail, and only until the 90 days are over.

Verification is constant time and answers ``None`` for every defect (bad shape, bad signature,
unknown version or scope, expired) so the caller cannot tell them apart. A token made for another
user does not verify as that user: the id is inside the signed payload.
"""

import base64
import binascii
import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from classbeat.auth.keys import Keys

LIFETIME = timedelta(days=90)
VERSION = "v1"
UNSUBSCRIBE_ALL = "all"
# The switchable categories (``emails.registry``; ``tests/test_mail_prefs.py`` keeps the two lists
# equal). "essential" is deliberately not here.
CATEGORIES = ("welcome", "account_activity", "class_ready")
SCOPES = frozenset((*CATEGORIES, UNSUBSCRIBE_ALL))
TOKEN_MAX = 512


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(keys: Keys, payload: bytes) -> bytes:
    return hmac.new(keys.mail_unsubscribe, payload, sha256).digest()


def issue_unsubscribe_token(
    keys: Keys, user_id: UUID, scope: str, *, now: datetime | None = None
) -> str:
    """A token that turns ``scope`` off for ``user_id`` for the next 90 days."""
    if scope not in SCOPES:
        raise ValueError("unsubscribe scope is not switchable")
    expires = int(((now or datetime.now(UTC)) + LIFETIME).timestamp())
    payload = f"{VERSION}|{user_id}|{scope}|{expires}".encode("ascii")
    return f"{_b64(payload)}.{_b64(_mac(keys, payload))}"


def verify_unsubscribe_token(
    keys: Keys, token: str, *, now: datetime | None = None
) -> tuple[UUID, str] | None:
    """The user and scope of a genuine, unexpired token, else ``None``."""
    if not token or len(token) > TOKEN_MAX or token.count(".") != 1:
        return None
    body, _, signature = token.partition(".")
    try:
        payload, supplied = _unb64(body), _unb64(signature)
    except (binascii.Error, ValueError):
        return None
    if not hmac.compare_digest(supplied, _mac(keys, payload)):
        return None
    try:
        version, raw_user, scope, raw_expiry = payload.decode("ascii").split("|")
        user_id, expires = UUID(raw_user), int(raw_expiry)
    except ValueError:
        return None
    if version != VERSION or scope not in SCOPES:
        return None
    if (now or datetime.now(UTC)).timestamp() >= expires:
        return None
    return user_id, scope
