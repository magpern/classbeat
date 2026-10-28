"""Envelope encryption: AES-256-GCM payloads under a per-write data key, wrapped by a KEK.

ADR 0004 §2. Pure: no database, no files, no logging. Every encryption, decryption, wrap and
unwrap of classbeat goes through here. Only ``classbeat.connections``, ``classbeat.auth`` (TOTP)
and ``classbeat.crypto.keks`` (the key check) may import it (``tests/test_crypto_guard.py``).

- A **data key** (DEK, 32 random bytes) is new for every write and encrypts exactly one payload
  with a random 96-bit nonce, so nonce reuse under one key cannot happen.
- The DEK is **wrapped** with AES-256-GCM under the KEK named by ``key_id``, its own random
  nonce and AAD that names the key and the row. A wrapped DEK is 48 bytes (32 + the GCM tag).
- AAD strings join fields with ``|``; no field can contain one (UUIDs in canonical text form,
  provider names from a check constraint, ``key_id`` from its pattern), so they cannot collide.

Any authentication failure raises ``DecryptFailed`` with no detail, and nothing else is tried.
"""

import contextlib
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, NoReturn
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_LENGTH = 32
NONCE_LENGTH = 12  # 96 bits
WRAPPED_DEK_LENGTH = KEY_LENGTH + 16  # the GCM tag
CHECK_VALUE_LENGTH = 16
KEY_ID_PATTERN = re.compile(r"kek-[a-z0-9-]{1,28}")
_CHECK_MESSAGE = b"classbeat-kek-check-v1"
_PROVIDERS = frozenset({"mowl", "spotify", "swedebeat"})


class DecryptFailed(Exception):
    """A GCM authentication failure (wrong key, AAD or tampered bytes). Carries no detail."""

    def __init__(self) -> None:
        super().__init__("decrypt_failed")


def is_key_id(value: object) -> bool:
    """Whether ``value`` matches the KEK id pattern ``^kek-[a-z0-9-]{1,28}$``."""
    return isinstance(value, str) and KEY_ID_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class Kek:
    """A key-encryption key and its id. ``repr`` never shows the key; never pickled."""

    key_id: str
    key: bytes

    def __post_init__(self) -> None:
        if not is_key_id(self.key_id):
            raise ValueError("the KEK id does not match ^kek-[a-z0-9-]{1,28}$")
        if len(self.key) != KEY_LENGTH:
            raise ValueError("a KEK is exactly 32 bytes")

    def __repr__(self) -> str:
        return f"Kek(key_id={self.key_id!r}, key=...)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("a KEK cannot be pickled")


@dataclass(frozen=True, slots=True)
class Sealed:
    """What is stored: the payload ciphertext and nonce, and the wrapped DEK and its nonce."""

    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    dek_nonce: bytes
    key_id: str

    def __repr__(self) -> str:
        return f"Sealed(key_id={self.key_id!r}, ...)"


def check_value(key: bytes) -> bytes:
    """``HMAC-SHA256(KEK, "classbeat-kek-check-v1")``, first 16 bytes (the ``connection_keks``
    row that detects a swapped key under an unchanged id)."""
    return hmac.new(key, _CHECK_MESSAGE, hashlib.sha256).digest()[:CHECK_VALUE_LENGTH]


def canonical_json(obj: Any) -> bytes:
    """The one JSON encoding of a payload."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _uuid(value: UUID) -> str:
    if not isinstance(value, UUID):
        raise TypeError("a UUID is required")
    return str(value)  # canonical: lower case, hyphenated, 36 characters


def _provider(value: str) -> str:
    if value not in _PROVIDERS:
        raise ValueError("unknown provider")
    return value


def _key_id(value: str) -> str:
    if not is_key_id(value):
        raise ValueError("the KEK id does not match ^kek-[a-z0-9-]{1,28}$")
    return value


def connection_aad(owner_id: UUID, provider: str, row_id: UUID) -> bytes:
    """``classbeat-conn-v1|<owner_id>|<provider>|<id>``: the payload of a connection row."""
    return f"classbeat-conn-v1|{_uuid(owner_id)}|{_provider(provider)}|{_uuid(row_id)}".encode()


def connection_wrap_aad(key_id: str, owner_id: UUID, provider: str, row_id: UUID) -> bytes:
    """``classbeat-dek-v1|<key_id>|<owner_id>|<provider>|<id>``: the wrapped DEK of that row."""
    return (
        f"classbeat-dek-v1|{_key_id(key_id)}|{_uuid(owner_id)}|{_provider(provider)}"
        f"|{_uuid(row_id)}"
    ).encode()


def totp_aad(user_id: UUID) -> bytes:
    """``classbeat-totp-v2|<user_id>``: a v2 TOTP secret."""
    return f"classbeat-totp-v2|{_uuid(user_id)}".encode()


def totp_wrap_aad(key_id: str, user_id: UUID) -> bytes:
    """``classbeat-totp-dek-v1|<key_id>|<user_id>``: the wrapped DEK of a v2 TOTP secret."""
    return f"classbeat-totp-dek-v1|{_key_id(key_id)}|{_uuid(user_id)}".encode()


def _decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes) -> bytes:
    plaintext: bytes | None = None
    # Raised after the block, so DecryptFailed carries no __context__.
    with contextlib.suppress(InvalidTag, ValueError):
        plaintext = AESGCM(key).decrypt(nonce, data, aad)
    if plaintext is None:
        raise DecryptFailed
    return plaintext


def wrap(dek: bytes, kek: Kek, wrap_aad: bytes) -> tuple[bytes, bytes]:
    """``(wrapped_dek, dek_nonce)``: the DEK under ``kek``, a fresh random nonce."""
    if len(dek) != KEY_LENGTH:
        raise ValueError("a data key is exactly 32 bytes")
    nonce = secrets.token_bytes(NONCE_LENGTH)
    return AESGCM(kek.key).encrypt(nonce, dek, wrap_aad), nonce


def unwrap(wrapped_dek: bytes, dek_nonce: bytes, kek: Kek, wrap_aad: bytes) -> bytes:
    """The DEK, or ``DecryptFailed``."""
    dek = _decrypt(kek.key, bytes(dek_nonce), bytes(wrapped_dek), wrap_aad)
    if len(dek) != KEY_LENGTH:
        raise DecryptFailed
    return dek


def seal(plaintext: bytes, aad: bytes, kek: Kek, wrap_aad: bytes) -> Sealed:
    """Encrypt ``plaintext`` under a new DEK and wrap the DEK under ``kek``."""
    dek = secrets.token_bytes(KEY_LENGTH)
    nonce = secrets.token_bytes(NONCE_LENGTH)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
    wrapped, dek_nonce = wrap(dek, kek, wrap_aad)
    return Sealed(ciphertext, nonce, wrapped, dek_nonce, kek.key_id)


def open_sealed(
    ciphertext: bytes,
    nonce: bytes,
    wrapped_dek: bytes,
    dek_nonce: bytes,
    kek: Kek,
    aad: bytes,
    wrap_aad: bytes,
) -> bytes:
    """Unwrap the DEK and decrypt the payload, or ``DecryptFailed``."""
    dek = unwrap(wrapped_dek, dek_nonce, kek, wrap_aad)
    return _decrypt(dek, bytes(nonce), bytes(ciphertext), aad)


def rewrap(
    wrapped_dek: bytes,
    dek_nonce: bytes,
    old: Kek,
    old_wrap_aad: bytes,
    new: Kek,
    new_wrap_aad: bytes,
) -> tuple[bytes, bytes]:
    """Move a wrapped DEK from ``old`` to ``new``. The DEK and the payload stay the same."""
    return wrap(unwrap(wrapped_dek, dek_nonce, old, old_wrap_aad), new, new_wrap_aad)
