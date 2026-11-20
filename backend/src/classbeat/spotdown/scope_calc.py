"""``cache_scope`` and ``Idempotency-Key`` for spotdown (ADR 0040 section 7, contract "Common
rules"). Pure: no I/O.

**``cache_scope``** is an HMAC-SHA256 of the owner id, never the raw id: spotdown learns a stable
opaque scope per owner, from which it cannot recover or link the owner id. The key is the caller's
(``SCOPE_KEY_INFO`` names the HKDF info string of ``classbeat.auth.derive_key`` over the app
secret that the wiring PR uses). The value is ``o1-`` plus the unpadded base64url digest
(46 characters of ``[A-Za-z0-9_-]``, inside the contract's 1 to 128). Rotating that key gives
every owner a new scope; private analyses under the old scope are then orphaned in spotdown
(and not reached by a ``DELETE /v2/scopes/{scope}`` of the new one), so a rotation must delete
the old scopes first.

**``Idempotency-Key``** is derived from the caller's stable parts (for example the job id, the
step and the track's client ref), so a retried step sends the same key and spotdown returns its
first answer; the parts are hashed, so none of them is sent as written.
"""

import base64
import hashlib
import hmac
import re
from typing import Final
from uuid import UUID

SCOPE_KEY_INFO: Final = "spotdown-cache-scope"
SCOPE_PREFIX: Final = "o1-"
SCOPE_KEY_MIN: Final = 32
_SCOPE_LABEL: Final = b"classbeat/spotdown-scope/v1\x00"
_IDEMPOTENCY_LABEL: Final = b"classbeat/spotdown-idempotency/v1\x00"
SCOPE_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")
IDEMPOTENCY_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


def cache_scope(key: bytes, owner_id: UUID) -> str:
    """The owner's opaque scope: ``o1-`` + base64url(HMAC-SHA256(key, label || owner id))."""
    if not isinstance(owner_id, UUID):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("cache_scope takes the owner's UUID")
    if len(key) < SCOPE_KEY_MIN:
        raise ValueError(f"the scope key is at least {SCOPE_KEY_MIN} bytes")
    digest = hmac.new(key, _SCOPE_LABEL + owner_id.bytes, hashlib.sha256).digest()
    return SCOPE_PREFIX + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def idempotency_key(*parts: str) -> str:
    """64 hex characters from ``parts`` (at least one, each non-empty, no NUL)."""
    if not parts or any(not p or "\x00" in p for p in parts):
        raise ValueError("an idempotency key needs non-empty parts without NUL")
    joined = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(_IDEMPOTENCY_LABEL + joined).hexdigest()
