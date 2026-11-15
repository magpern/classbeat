"""The pairwise subject (ADR 0034 section 1). Pure: no I/O.

``sub = base64url-nopad(HMAC-SHA256(pairwise_key, UTF-8(client_id) || 0x00 || users.id))``, with
the id as its 16 bytes in RFC 4122 order (``UUID.bytes``): always 43 characters. The key never
rotates (that would change every subject the player knows).
"""

import hashlib
import hmac
from base64 import urlsafe_b64encode
from uuid import UUID

KEY_BYTES = 32
SUBJECT_LENGTH = 43


def pairwise_subject(key: bytes, client_id: str, user_id: UUID) -> str:
    if len(key) != KEY_BYTES:
        raise ValueError("the pairwise key must be 32 bytes")
    message = client_id.encode("utf-8") + b"\x00" + user_id.bytes
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
